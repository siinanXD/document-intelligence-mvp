"""CLI: python -m app.evaluation

Default run uses hashing embeddings and the retrieval-v1 golden corpus.
Generation evaluation is `--track generation` with the scripted LLM.

Live provider runs (`--embeddings live`, `--llm live`, `--judge live`) are
opt-in, bounded, and never part of ordinary CI.

The run uses a single database transaction and rolls it back: evaluation
tenants and documents do not persist. Qdrant is in-memory.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.settings import get_settings
from app.evaluation.cases import load_dataset, load_generation_dataset
from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.generation_runner import (
    LIVE_DEFAULT_MAX_CASES,
    LIVE_DEFAULT_MAX_COST_USD,
    run_generation_dataset,
)
from app.evaluation.ingest import ingest_dataset
from app.evaluation.judge import LiveJudge, NullJudge
from app.evaluation.report import (
    build_generation_report,
    build_report,
    dump_report,
    format_summary,
    git_commit,
    load_report,
)
from app.evaluation.runner import run_dataset
from app.evaluation.scripted_llm import ScriptedLLM
from app.providers.local_storage import LocalStorageBackend
from app.providers.prompts import ASK_GROUNDED
from app.services.vector_store import VectorStoreService

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RETRIEVAL_BASELINE = REPO_ROOT / "evaluation" / "baselines" / "retrieval-v1.json"
DEFAULT_RETRIEVAL_OUTPUT = REPO_ROOT / "evaluation" / "reports" / "retrieval-v1.json"
DEFAULT_GENERATION_BASELINE = REPO_ROOT / "evaluation" / "baselines" / "generation-v1.json"
DEFAULT_GENERATION_OUTPUT = REPO_ROOT / "evaluation" / "reports" / "generation-v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the golden evaluation.")
    parser.add_argument(
        "--track",
        choices=("retrieval", "generation"),
        default="retrieval",
        help="retrieval is the CI default. generation scores grounded /ask.",
    )
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--mode", choices=("all", "semantic", "lexical"), default="all")
    parser.add_argument(
        "--embeddings",
        choices=("hashing", "live"),
        default="hashing",
        help="hashing is the CI default. live calls the configured provider and is opt-in.",
    )
    parser.add_argument(
        "--llm",
        choices=("scripted", "live"),
        default="scripted",
        help="scripted is the CI default for generation. live is opt-in.",
    )
    parser.add_argument(
        "--judge",
        choices=("none", "live"),
        default="none",
        help="Optional groundedness/completeness judge. Never the release decision.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Cap cases for a live run. Defaults to 8 when --llm live or --judge live.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="Stop a live run after this estimated cost. Defaults to 0.50 when live.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Do not compare against the checked-in baseline.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL URL. Defaults to TEST_DATABASE_URL, then DATABASE_URL.",
    )
    return parser


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.environ.get("TEST_DATABASE_URL") or get_settings().database_url


def _is_live(args: argparse.Namespace) -> bool:
    return args.embeddings == "live" or args.llm == "live" or args.judge == "live"


def _apply_live_bounds(args: argparse.Namespace) -> None:
    if not _is_live(args):
        return
    if args.max_cases is None:
        args.max_cases = LIVE_DEFAULT_MAX_CASES
    if args.max_cost_usd is None:
        args.max_cost_usd = LIVE_DEFAULT_MAX_COST_USD
    if (args.llm == "live" or args.judge == "live") and args.baseline is None:
        # Live answers do not match the scripted hashing baseline.
        args.no_compare = True


async def _async_main(args: argparse.Namespace) -> int:
    _apply_live_bounds(args)
    if args.track == "generation":
        return await _run_generation(args)
    return await _run_retrieval(args)


def _embeddings(kind: str):
    if kind == "live":
        from app.providers.registry import get_embedding_provider

        return get_embedding_provider()
    return HashingEmbeddings()


async def _run_retrieval(args: argparse.Namespace) -> int:
    dataset_name = args.dataset or "retrieval-v1"
    dataset = load_dataset(dataset_name)
    embeddings = _embeddings(args.embeddings)
    output = args.output or DEFAULT_RETRIEVAL_OUTPUT
    baseline = args.baseline or DEFAULT_RETRIEVAL_BASELINE

    engine = create_async_engine(_database_url(args.database_url))
    client = AsyncQdrantClient(":memory:")
    vector_store = VectorStoreService(
        client=client, collection=f"eval_{dataset.name}_{embeddings.version}"
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                with tempfile.TemporaryDirectory(prefix="eval-storage-") as tmp:
                    storage = LocalStorageBackend(tmp)
                    documents = await ingest_dataset(
                        session, storage, dataset, embeddings, vector_store
                    )
                    results = await run_dataset(
                        session,
                        dataset,
                        documents,
                        embeddings,
                        vector_store,
                        mode=args.mode,
                    )
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await client.close()
        await engine.dispose()

    modes = {name: result.summary for name, result in sorted(results.items())}
    report = build_report(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        commit=git_commit(REPO_ROOT),
        embeddings=embeddings,
        modes=modes,
    )
    dump_report(report, output)
    print(format_summary(report))
    print(f"wrote {output}")
    return _compare_or_leakage(report, baseline, args)


async def _run_generation(args: argparse.Namespace) -> int:
    dataset_name = args.dataset or "generation-v1"
    dataset = load_generation_dataset(dataset_name)
    embeddings = _embeddings(args.embeddings)
    output = args.output or DEFAULT_GENERATION_OUTPUT
    baseline = args.baseline or DEFAULT_GENERATION_BASELINE
    if args.llm == "live":
        from app.providers.registry import get_llm_provider

        llm = get_llm_provider()
    else:
        llm = ScriptedLLM(dataset.cases)

    if args.judge == "live":
        from app.providers.registry import get_llm_provider

        judge = LiveJudge(get_llm_provider())
        judge_name = "live"
    else:
        judge = NullJudge()
        judge_name = "none"

    engine = create_async_engine(_database_url(args.database_url))
    client = AsyncQdrantClient(":memory:")
    vector_store = VectorStoreService(
        client=client, collection=f"eval_{dataset.name}_{embeddings.version}"
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                with tempfile.TemporaryDirectory(prefix="eval-storage-") as tmp:
                    storage = LocalStorageBackend(tmp)
                    documents = await ingest_dataset(
                        session, storage, dataset, embeddings, vector_store
                    )
                    result = await run_generation_dataset(
                        session,
                        dataset,
                        documents,
                        embeddings,
                        llm,
                        vector_store,
                        judge=judge,
                        max_cases=args.max_cases,
                        max_cost_usd=args.max_cost_usd if _is_live(args) else None,
                    )
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await client.close()
        await engine.dispose()

    report = build_generation_report(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        commit=git_commit(REPO_ROOT),
        embeddings=embeddings,
        llm=llm,
        prompt_name=ASK_GROUNDED.name,
        prompt_version=ASK_GROUNDED.version,
        judge=judge_name,
        summary=result.summary.as_dict(),
        stopped_reason=result.stopped_reason,
    )
    dump_report(report, output)
    print(format_summary(report))
    print(f"wrote {output}")
    return _compare_or_leakage(report, baseline, args)


def _compare_or_leakage(report: dict, baseline: Path, args: argparse.Namespace) -> int:
    if report.get("track") == "generation":
        leakage = int((report.get("summary") or {}).get("cross_tenant_leakage") or 0)
        leakage += int((report.get("summary") or {}).get("foreign_source_ids") or 0)
        leakage += int((report.get("summary") or {}).get("unresolvable_citations") or 0)
    else:
        leakage = sum(
            int(body.get("cross_tenant_leakage") or 0)
            for body in (report.get("modes") or {}).values()
        )
    if args.no_compare or not baseline.is_file():
        return 2 if leakage else 0

    comparison = compare_reports(report, load_report(baseline))
    if comparison.changed_cases:
        print(f"changed cases: {len(comparison.changed_cases)}")
        for change in comparison.changed_cases[:20]:
            print(f"  {change}")
    for delta_mode, values in comparison.deltas.items():
        print(f"delta {delta_mode}: {values}")
    if not comparison.passed:
        for failure in comparison.threshold_failures:
            print(f"FAIL {failure}")
        return 2 if comparison.leakage else 1
    print("baseline comparison passed")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
