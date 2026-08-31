"""Versioned evaluation case and dataset schema.

A case is query → relevant evidence. Generated answers are out of scope.
Source ids are assigned at ingest, so relevance is stored as stable
filename + substring judgments and resolved against PostgreSQL after
chunking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

RetrievalMode = Literal["semantic", "lexical"]


@dataclass(frozen=True)
class EvidenceJudgment:
    document: str
    contains: str


@dataclass(frozen=True)
class EvalCase:
    id: str
    language: str
    category: str
    query: str
    tenant: str
    retrieval_mode: RetrievalMode
    relevant: tuple[EvidenceJudgment, ...]
    forbidden_documents: tuple[str, ...]
    notes: str
    track: str = "retrieval"


@dataclass(frozen=True)
class DatasetDocument:
    tenant: str
    source: str
    filename: str
    format: str


@dataclass(frozen=True)
class EvalDataset:
    name: str
    version: str
    track: str
    description: str
    documents: tuple[DatasetDocument, ...]
    cases: tuple[EvalCase, ...]
    root: Path

    def documents_for(self, tenant: str) -> tuple[DatasetDocument, ...]:
        return tuple(item for item in self.documents if item.tenant == tenant)

    def cases_for(self, *, mode: RetrievalMode | Literal["all"] = "all") -> tuple[EvalCase, ...]:
        if mode == "all":
            return self.cases
        return tuple(case for case in self.cases if case.retrieval_mode == mode)


def datasets_root() -> Path:
    """Repo-root `evaluation/datasets`, independent of the process cwd."""
    return Path(__file__).resolve().parents[2] / "evaluation" / "datasets"


def load_dataset(name: str = "retrieval-v1", *, root: Path | None = None) -> EvalDataset:
    directory = (root or datasets_root()) / name
    manifest_path = directory / "manifest.json"
    cases_path = directory / "cases.json"
    manifest = _load_json(manifest_path)
    raw_cases = _load_json(cases_path)
    if not isinstance(raw_cases, list):
        raise ValueError(f"{cases_path} must contain a JSON array")

    documents = tuple(
        DatasetDocument(
            tenant=str(item["tenant"]),
            source=str(item["source"]),
            filename=str(item["filename"]),
            format=str(item["format"]),
        )
        for item in manifest["documents"]
    )
    cases = tuple(_parse_case(item) for item in raw_cases)
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    return EvalDataset(
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        track=str(manifest.get("track", "retrieval")),
        description=str(manifest.get("description", "")),
        documents=documents,
        cases=cases,
        root=directory,
    )


def _parse_case(item: dict[str, Any]) -> EvalCase:
    mode = item["retrieval_mode"]
    if mode not in ("semantic", "lexical"):
        raise ValueError(f"unsupported retrieval_mode: {mode}")
    relevant = tuple(
        EvidenceJudgment(document=str(entry["document"]), contains=str(entry["contains"]))
        for entry in item.get("relevant") or []
    )
    forbidden = tuple(str(name) for name in item.get("forbidden_documents") or [])
    return EvalCase(
        id=str(item["id"]),
        language=str(item["language"]),
        category=str(item["category"]),
        query=str(item["query"]),
        tenant=str(item["tenant"]),
        retrieval_mode=mode,
        relevant=relevant,
        forbidden_documents=forbidden,
        notes=str(item.get("notes") or ""),
        track=str(item.get("track") or "retrieval"),
    )


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))
