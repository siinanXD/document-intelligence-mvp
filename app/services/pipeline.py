"""Map stored document state onto the cockpit pipeline.

The sequence is uploaded → parsed → chunked → embedded → indexed → ready.
The frontend displays this; it does not invent stages. Failure reasons are
classified types, never the diagnostic string that might quote a document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import Document, DocumentStatus, IngestionJob

PIPELINE_STAGES = ("uploaded", "parsed", "chunked", "embedded", "indexed", "ready")


@dataclass(frozen=True)
class PipelineStage:
    id: str
    complete: bool
    current: bool

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "complete": self.complete, "current": self.current}


@dataclass(frozen=True)
class PipelineView:
    stages: tuple[PipelineStage, ...]
    current_stage: str
    failed: bool
    failure_class: str | None
    error_type: str | None
    chunk_count: int
    parser_name: str | None
    embedding_provider: str | None
    embedding_model: str | None
    embedding_version: str | None
    job_status: str | None
    job_attempts: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stages": [stage.as_dict() for stage in self.stages],
            "current_stage": self.current_stage,
            "failed": self.failed,
            "failure_class": self.failure_class,
            "error_type": self.error_type,
            "chunk_count": self.chunk_count,
            "parser_name": self.parser_name,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_version": self.embedding_version,
            "job_status": self.job_status,
            "job_attempts": self.job_attempts,
        }


def classify_failure(reason: str | None) -> tuple[str | None, str | None]:
    """Return (failure_class, error_type). Never returns the raw reason."""
    if not reason:
        return None, None
    token = reason.split(":", 1)[0].strip()
    error_type = token.split()[-1] if token else "Error"
    lowered = reason.lower()
    if "vectorstore" in lowered or "qdrant" in lowered:
        klass = "vector_store"
    elif "providerconfiguration" in lowered or "not configured" in lowered:
        klass = "configuration"
    elif any(part in lowered for part in ("openai", "embedding", "llm", "provider")):
        klass = "provider"
    elif "database" in lowered or "postgres" in lowered:
        klass = "database"
    else:
        klass = "document_processing"
    return klass, error_type


def build_pipeline(
    *,
    document: Document,
    chunk_count: int,
    job: IngestionJob | None,
) -> PipelineView:
    parsed = bool(document.parser_name or document.normalized_key or document.content_hash)
    chunked = chunk_count > 0
    embedded = document.embedding_provider is not None
    indexed = document.embedding_dimensions is not None
    ready = document.status == DocumentStatus.ready
    done = {
        "uploaded": True,
        "parsed": parsed,
        "chunked": chunked,
        "embedded": embedded,
        "indexed": indexed,
        "ready": ready,
    }
    current = "ready"
    for stage in PIPELINE_STAGES:
        if not done[stage]:
            current = stage
            break
    failed = document.status == DocumentStatus.failed
    failure_class = None
    error_type = None
    if failed:
        failure_class, error_type = classify_failure(None if job is None else job.last_error)
    stages = tuple(
        PipelineStage(id=stage, complete=done[stage], current=stage == current)
        for stage in PIPELINE_STAGES
    )
    return PipelineView(
        stages=stages,
        current_stage=current,
        failed=failed,
        failure_class=failure_class,
        error_type=error_type,
        chunk_count=chunk_count,
        parser_name=document.parser_name,
        embedding_provider=document.embedding_provider,
        embedding_model=document.embedding_model,
        embedding_version=document.embedding_version,
        job_status=None if job is None else job.status.value,
        job_attempts=None if job is None else job.attempts,
    )
