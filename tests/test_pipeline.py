"""Pipeline status, classified failures, and cross-tenant absence."""

import uuid

from app.models import Document, DocumentStatus, IngestionJob, JobStatus
from app.services.pipeline import build_pipeline, classify_failure


def test_classify_failure_returns_type_not_payload():
    klass, error_type = classify_failure("ParsingError: quoted the document")
    assert klass == "document_processing"
    assert error_type == "ParsingError"


def test_classify_failure_maps_vector_store_and_configuration():
    assert classify_failure("VectorStoreError: timeout")[0] == "vector_store"
    assert classify_failure("ProviderConfigurationError")[0] == "configuration"
    assert classify_failure("unexpected OperationalError")[1] == "OperationalError"


def test_queued_document_is_at_uploaded():
    document = Document(
        tenant_id=uuid.uuid4(),
        filename="a.pdf",
        mime_type="application/pdf",
        storage_key="k",
        file_hash="a" * 64,
        status=DocumentStatus.queued,
    )
    view = build_pipeline(document=document, chunk_count=0, job=None)
    assert view.current_stage == "parsed"
    assert view.failed is False
    assert [stage.id for stage in view.stages if stage.complete] == ["uploaded"]


def test_ready_document_completes_every_stage():
    document = Document(
        tenant_id=uuid.uuid4(),
        filename="a.pdf",
        mime_type="application/pdf",
        storage_key="k",
        file_hash="a" * 64,
        status=DocumentStatus.ready,
        parser_name="stub",
        normalized_key="k.json",
        content_hash="b" * 64,
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimensions=1536,
    )
    view = build_pipeline(document=document, chunk_count=3, job=None)
    assert view.current_stage == "ready"
    assert all(stage.complete for stage in view.stages)
    assert view.chunk_count == 3


def test_failed_job_exposes_class_not_raw_reason():
    document = Document(
        tenant_id=uuid.uuid4(),
        filename="a.pdf",
        mime_type="application/pdf",
        storage_key="k",
        file_hash="a" * 64,
        status=DocumentStatus.failed,
    )
    job = IngestionJob(
        tenant_id=document.tenant_id,
        document_id=uuid.uuid4(),
        status=JobStatus.failed,
        attempts=3,
        last_error="ParsingError: never include the customer filename.pdf",
    )
    view = build_pipeline(document=document, chunk_count=0, job=job)
    assert view.failed is True
    assert view.failure_class == "document_processing"
    assert view.error_type == "ParsingError"
    dumped = view.as_dict()
    assert "filename.pdf" not in str(dumped)
    assert dumped["job_attempts"] == 3
