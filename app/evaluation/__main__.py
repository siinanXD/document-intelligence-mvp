"""CLI: python -m app.evaluation

Default run uses hashing embeddings and the retrieval-v1 golden corpus. A
live OpenAI run is opt-in (`--embeddings live`) and never part of ordinary CI.

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
from app.evaluation.cases import load_dataset
from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.ingest import ingest_dataset
from app.evaluation.report import build_report, dump_report, format_summary, git_commit, load_report
from app.evaluation.runner import run_dataset
from app.providers.local_storage import LocalStorageBackend
from app.services.vector_store import VectorStoreService

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPO_ROOT / "evaluation" / "baselines" / "retrieval-v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "evaluation" / "reports" / "retrieval-v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the retrieval golden evaluation.")
    parser.add_argument("--dataset", default="retrieval-v1")
    parser.add_argument("--mode", choices=("all", "semantic", "lexical"), default="all")
    parser.add_argument(
        "--embeddings",
        choices=("hashing", "live"),
        default="hashing",
        help="hashing is the CI default. live calls the configured provider and is opt-in.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
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


async def _async_main(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    if args.embeddings == "live":
        from app.providers.registry import get_embedding_provider

        embeddings = get_embedding_provider()
    else:
        embeddings = HashingEmbeddings()

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
    dump_report(report, args.output)
    print(format_summary(report))
    print(f"wrote {args.output}")

    leakage = sum(int(body.get("cross_tenant_leakage") or 0) for body in modes.values())
    if args.no_compare or not args.baseline.is_file():
        return 2 if leakage else 0

    comparison = compare_reports(report, load_report(args.baseline))
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
