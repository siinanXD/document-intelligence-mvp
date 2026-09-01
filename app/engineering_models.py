"""Canonical machine and engineering evidence model (SIN-89).

These tables extend the existing document/chunk source of truth. They do not
replace documents, and they do not store vendor SDKs or graph-database state.
Vendor-specific designations live in JSONB `attributes`, not as core columns.

Every tenant-owned row carries `tenant_id`. Child foreign keys are composite
`(tenant_id, parent_id)` so a row cannot attach to another tenant's parent.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PackageStatus(enum.StrEnum):
    draft = "draft"
    needs_review = "needs_review"
    ready = "ready"


class IdentityStatus(enum.StrEnum):
    unresolved = "unresolved"
    resolved = "resolved"
    conflicted = "conflicted"


class AssignmentState(enum.StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    needs_review = "needs_review"
    superseded = "superseded"
    rejected = "rejected"


class EntityKind(enum.StrEnum):
    component = "component"
    signal = "signal"
    port = "port"
    terminal = "terminal"
    cable = "cable"
    connection = "connection"
    other = "other"


class EngineeringRelationKind(enum.StrEnum):
    connected_to = "connected_to"
    maps_to = "maps_to"
    contains = "contains"
    calls = "calls"
    reads = "reads"
    writes = "writes"
    related = "related"


class EvidenceLocatorKind(enum.StrEnum):
    chunk = "chunk"
    page = "page"
    sheet_cell = "sheet_cell"
    image_region = "image_region"
    xml_path = "xml_path"
    line_range = "line_range"
    native_id = "native_id"


class EvidenceSubjectKind(enum.StrEnum):
    package_assignment = "package_assignment"
    entity = "entity"
    entity_candidate = "entity_candidate"
    relation = "relation"
    relation_candidate = "relation_candidate"
    plc_program = "plc_program"
    plc_block = "plc_block"
    plc_variable = "plc_variable"
    plc_reference = "plc_reference"
    conflict = "conflict"
    unsupported_construct = "unsupported_construct"
    behavior_claim = "behavior_claim"
    human_override = "human_override"


class PlcDialect(enum.StrEnum):
    tia_s7 = "tia_s7"
    s5 = "s5"
    plcopen = "plcopen"
    unknown = "unknown"


class PlcReferenceKind(enum.StrEnum):
    read = "read"
    write = "write"
    call = "call"
    condition = "condition"


class ConflictKind(enum.StrEnum):
    identity = "identity"
    connectivity = "connectivity"
    mapping = "mapping"
    revision = "revision"
    other = "other"


class ConflictStatus(enum.StrEnum):
    open = "open"
    resolved = "resolved"
    accepted_ambiguity = "accepted_ambiguity"


class BehaviorClaimKind(enum.StrEnum):
    sequence = "sequence"
    interlock = "interlock"
    alarm_chain = "alarm_chain"
    other = "other"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def _confidence() -> Mapped[float]:
    return mapped_column(Float, nullable=False)


def _method() -> Mapped[str]:
    return mapped_column(String(64), nullable=False)


def _method_version() -> Mapped[str]:
    return mapped_column(String(64), nullable=False)


def _attributes() -> Mapped[dict]:
    return mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )


_CONFIDENCE_CHECK = "confidence >= 0 AND confidence <= 1"


class MachinePackage(Base):
    """A tenant's working set of artifacts about one machine (or line)."""

    __tablename__ = "machine_packages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PackageStatus] = mapped_column(
        Enum(PackageStatus, name="package_status", native_enum=True),
        nullable=False,
        default=PackageStatus.draft,
        server_default=PackageStatus.draft.value,
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_machine_packages_tenant_id_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_machine_packages_tenant_slug"),
        Index("ix_machine_packages_tenant", "tenant_id"),
    )


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_machines_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_machines_tenant_package",
            ondelete="CASCADE",
        ),
        Index(
            "uq_machines_package_code",
            "tenant_id",
            "package_id",
            "code",
            unique=True,
            postgresql_where=sql_text("code IS NOT NULL"),
        ),
        Index("ix_machines_tenant_package", "tenant_id", "package_id"),
    )


class Assembly(Base):
    __tablename__ = "assemblies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    machine_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_assembly_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_assemblies_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_assemblies_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_assemblies_tenant_machine",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_assembly_id"],
            ["assemblies.tenant_id", "assemblies.id"],
            name="fk_assemblies_tenant_parent",
            ondelete="SET NULL",
        ),
        CheckConstraint("id <> parent_assembly_id", name="ck_assemblies_no_self_parent"),
        Index(
            "uq_assemblies_package_code",
            "tenant_id",
            "package_id",
            "code",
            unique=True,
            postgresql_where=sql_text("code IS NOT NULL"),
        ),
        Index("ix_assemblies_tenant_machine", "tenant_id", "machine_id"),
    )


class PackageDocument(Base):
    """Membership of an existing document in a machine package."""

    __tablename__ = "package_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_package_documents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "package_id",
            "document_id",
            name="uq_package_documents_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_package_documents_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_package_documents_tenant_document",
            ondelete="CASCADE",
        ),
        Index("ix_package_documents_tenant_document", "tenant_id", "document_id"),
    )


class PackageAssignment(Base):
    """Evidence-weighted claim that a document belongs to a machine/assembly."""

    __tablename__ = "package_assignments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assembly_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    state: Mapped[AssignmentState] = mapped_column(
        Enum(AssignmentState, name="assignment_state", native_enum=True),
        nullable=False,
        default=AssignmentState.proposed,
        server_default=AssignmentState.proposed.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    reason: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_package_assignments_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_package_assignments_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_package_assignments_tenant_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_package_assignments_tenant_machine",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assembly_id"],
            ["assemblies.tenant_id", "assemblies.id"],
            name="fk_package_assignments_tenant_assembly",
            ondelete="SET NULL",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_package_assignments_confidence"),
        Index("ix_package_assignments_tenant_document", "tenant_id", "document_id"),
    )


class EngineeringEntity(Base):
    """Canonical component, signal, port or similar. Distinct from candidates."""

    __tablename__ = "engineering_entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assembly_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_kind: Mapped[EntityKind] = mapped_column(
        Enum(EntityKind, name="entity_kind", native_enum=True), nullable=False
    )
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_engineering_entities_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "package_id",
            "id",
            name="uq_engineering_entities_tenant_package_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_engineering_entities_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_engineering_entities_tenant_machine",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assembly_id"],
            ["assemblies.tenant_id", "assemblies.id"],
            name="fk_engineering_entities_tenant_assembly",
            ondelete="SET NULL",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_engineering_entities_confidence"),
        Index("ix_engineering_entities_tenant_package", "tenant_id", "package_id"),
        Index("ix_engineering_entities_tenant_kind", "tenant_id", "entity_kind"),
    )


class EngineeringEntityCandidate(Base):
    """An observation that may become a canonical entity. Unresolved is data."""

    __tablename__ = "engineering_entity_candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_kind: Mapped[EntityKind] = mapped_column(
        Enum(EntityKind, name="entity_kind", native_enum=True), nullable=False
    )
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_entity_candidates_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_entity_candidates_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "canonical_entity_id"],
            ["engineering_entities.tenant_id", "engineering_entities.id"],
            name="fk_entity_candidates_tenant_canonical",
            ondelete="SET NULL",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_entity_candidates_confidence"),
        Index("ix_entity_candidates_tenant_package", "tenant_id", "package_id"),
    )


class EngineeringRelation(Base):
    """Canonical edge between two canonical entities of the same tenant/package."""

    __tablename__ = "engineering_relations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_kind: Mapped[EngineeringRelationKind] = mapped_column(
        Enum(EngineeringRelationKind, name="engineering_relation_kind", native_enum=True),
        nullable=False,
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_engineering_relations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_engineering_relations_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id", "source_entity_id"],
            [
                "engineering_entities.tenant_id",
                "engineering_entities.package_id",
                "engineering_entities.id",
            ],
            name="fk_engineering_relations_tenant_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id", "target_entity_id"],
            [
                "engineering_entities.tenant_id",
                "engineering_entities.package_id",
                "engineering_entities.id",
            ],
            name="fk_engineering_relations_tenant_target",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_engineering_relations_no_self_edge",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_engineering_relations_confidence"),
        UniqueConstraint(
            "tenant_id",
            "package_id",
            "source_entity_id",
            "target_entity_id",
            "relation_kind",
            name="uq_engineering_relations_edge",
        ),
        Index("ix_engineering_relations_tenant_package", "tenant_id", "package_id"),
    )


class EngineeringRelationCandidate(Base):
    __tablename__ = "engineering_relation_candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_relation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    relation_kind: Mapped[EngineeringRelationKind] = mapped_column(
        Enum(EngineeringRelationKind, name="engineering_relation_kind", native_enum=True),
        nullable=False,
    )
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_relation_candidates_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_relation_candidates_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "canonical_relation_id"],
            ["engineering_relations.tenant_id", "engineering_relations.id"],
            name="fk_relation_candidates_tenant_canonical",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id", "source_entity_id"],
            [
                "engineering_entities.tenant_id",
                "engineering_entities.package_id",
                "engineering_entities.id",
            ],
            name="fk_relation_candidates_tenant_source",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id", "target_entity_id"],
            [
                "engineering_entities.tenant_id",
                "engineering_entities.package_id",
                "engineering_entities.id",
            ],
            name="fk_relation_candidates_tenant_target",
            ondelete="SET NULL",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_relation_candidates_confidence"),
        Index("ix_relation_candidates_tenant_package", "tenant_id", "package_id"),
    )


class EvidenceReference(Base):
    """Resolvable locator back to a source artifact. Does not store passage text."""

    __tablename__ = "evidence_references"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    locator_kind: Mapped[EvidenceLocatorKind] = mapped_column(
        Enum(EvidenceLocatorKind, name="evidence_locator_kind", native_enum=True),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cell_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    xml_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_evidence_references_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_evidence_references_tenant_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chunks.tenant_id", "chunks.source_id"],
            name="fk_evidence_references_tenant_source_id",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number > 0",
            name="ck_evidence_references_page_positive",
        ),
        CheckConstraint(
            "line_start IS NULL OR line_start >= 0",
            name="ck_evidence_references_line_start",
        ),
        CheckConstraint(
            "line_end IS NULL OR line_start IS NULL OR line_end >= line_start",
            name="ck_evidence_references_line_range",
        ),
        CheckConstraint(
            "("
            "(locator_kind = 'chunk' AND document_id IS NOT NULL AND source_id IS NOT NULL) OR "
            "(locator_kind = 'page' AND document_id IS NOT NULL AND page_number IS NOT NULL) OR "
            "(locator_kind = 'sheet_cell' AND document_id IS NOT NULL "
            "AND sheet_name IS NOT NULL AND cell_range IS NOT NULL) OR "
            "(locator_kind = 'image_region' AND document_id IS NOT NULL "
            "AND region <> '{}'::jsonb) OR "
            "(locator_kind = 'xml_path' AND document_id IS NOT NULL "
            "AND xml_path IS NOT NULL) OR "
            "(locator_kind = 'line_range' AND document_id IS NOT NULL "
            "AND line_start IS NOT NULL) OR "
            "(locator_kind = 'native_id' AND document_id IS NOT NULL "
            "AND native_object_id IS NOT NULL)"
            ")",
            name="ck_evidence_references_locator",
        ),
        Index("ix_evidence_references_tenant_document", "tenant_id", "document_id"),
    )


class EvidenceBinding(Base):
    """Links one evidence locator to one derived subject in the same tenant."""

    __tablename__ = "evidence_bindings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_kind: Mapped[EvidenceSubjectKind] = mapped_column(
        Enum(EvidenceSubjectKind, name="evidence_subject_kind", native_enum=True),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "evidence_id",
            "subject_kind",
            "subject_id",
            name="uq_evidence_bindings_link",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_id"],
            ["evidence_references.tenant_id", "evidence_references.id"],
            name="fk_evidence_bindings_tenant_evidence",
            ondelete="CASCADE",
        ),
        Index("ix_evidence_bindings_tenant_subject", "tenant_id", "subject_kind", "subject_id"),
    )


class PlcProgram(Base):
    __tablename__ = "plc_programs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dialect: Mapped[PlcDialect] = mapped_column(
        Enum(PlcDialect, name="plc_dialect", native_enum=True),
        nullable=False,
        default=PlcDialect.unknown,
        server_default=PlcDialect.unknown.value,
    )
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plc_programs_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_plc_programs_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_plc_programs_tenant_machine",
            ondelete="SET NULL",
        ),
        Index("ix_plc_programs_tenant_package", "tenant_id", "package_id"),
    )


class PlcBlock(Base):
    """A POU/block. Network ordinals live on references, not a separate table."""

    __tablename__ = "plc_blocks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plc_blocks_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "program_id"],
            ["plc_programs.tenant_id", "plc_programs.id"],
            name="fk_plc_blocks_tenant_program",
            ondelete="CASCADE",
        ),
        UniqueConstraint("program_id", "name", name="uq_plc_blocks_program_name"),
        Index("ix_plc_blocks_tenant_program", "tenant_id", "program_id"),
    )


class PlcVariable(Base):
    __tablename__ = "plc_variables"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    block_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    io_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plc_variables_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "program_id"],
            ["plc_programs.tenant_id", "plc_programs.id"],
            name="fk_plc_variables_tenant_program",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "block_id"],
            ["plc_blocks.tenant_id", "plc_blocks.id"],
            name="fk_plc_variables_tenant_block",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["engineering_entities.tenant_id", "engineering_entities.id"],
            name="fk_plc_variables_tenant_entity",
            ondelete="SET NULL",
        ),
        Index("ix_plc_variables_tenant_program", "tenant_id", "program_id"),
        Index("ix_plc_variables_tenant_entity", "tenant_id", "entity_id"),
    )


class PlcReference(Base):
    """A read, write, call or condition located in a block/network."""

    __tablename__ = "plc_references"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    block_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variable_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reference_kind: Mapped[PlcReferenceKind] = mapped_column(
        Enum(PlcReferenceKind, name="plc_reference_kind", native_enum=True),
        nullable=False,
    )
    network_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_construct: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plc_references_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "block_id"],
            ["plc_blocks.tenant_id", "plc_blocks.id"],
            name="fk_plc_references_tenant_block",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "variable_id"],
            ["plc_variables.tenant_id", "plc_variables.id"],
            name="fk_plc_references_tenant_variable",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "network_ordinal IS NULL OR network_ordinal >= 0",
            name="ck_plc_references_network_ordinal",
        ),
        Index("ix_plc_references_tenant_block", "tenant_id", "block_id"),
    )


class EngineeringConflict(Base):
    __tablename__ = "engineering_conflicts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    conflict_kind: Mapped[ConflictKind] = mapped_column(
        Enum(ConflictKind, name="conflict_kind", native_enum=True), nullable=False
    )
    status: Mapped[ConflictStatus] = mapped_column(
        Enum(ConflictStatus, name="conflict_status", native_enum=True),
        nullable=False,
        default=ConflictStatus.open,
        server_default=ConflictStatus.open.value,
    )
    left_subject_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    left_subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    right_subject_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    right_subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_engineering_conflicts_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_engineering_conflicts_tenant_package",
            ondelete="CASCADE",
        ),
        Index("ix_engineering_conflicts_tenant_package", "tenant_id", "package_id"),
    )


class UnsupportedConstruct(Base):
    __tablename__ = "unsupported_constructs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    construct_code: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_unsupported_constructs_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_unsupported_constructs_tenant_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_unsupported_constructs_tenant_document",
            ondelete="SET NULL",
        ),
        Index("ix_unsupported_constructs_tenant_package", "tenant_id", "package_id"),
    )


class HumanOverride(Base):
    """A versioned correction. The original prediction row is not deleted."""

    __tablename__ = "human_overrides"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_kind: Mapped[EvidenceSubjectKind] = mapped_column(
        Enum(EvidenceSubjectKind, name="evidence_subject_kind", native_enum=True),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    new_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_human_overrides_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_human_overrides_tenant_package",
            ondelete="CASCADE",
        ),
        Index("ix_human_overrides_tenant_subject", "tenant_id", "subject_kind", "subject_id"),
    )


class DerivedBehaviorClaim(Base):
    """A grounded behavior chain. `dependency_path` holds identifiers only."""

    __tablename__ = "derived_behavior_claims"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claim_kind: Mapped[BehaviorClaimKind] = mapped_column(
        Enum(BehaviorClaimKind, name="behavior_claim_kind", native_enum=True),
        nullable=False,
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(IdentityStatus, name="identity_status", native_enum=True),
        nullable=False,
        default=IdentityStatus.unresolved,
        server_default=IdentityStatus.unresolved.value,
    )
    confidence: Mapped[float] = _confidence()
    method: Mapped[str] = _method()
    method_version: Mapped[str] = _method_version()
    dependency_path: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    attributes: Mapped[dict] = _attributes()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_behavior_claims_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["machine_packages.tenant_id", "machine_packages.id"],
            name="fk_behavior_claims_tenant_package",
            ondelete="CASCADE",
        ),
        CheckConstraint(_CONFIDENCE_CHECK, name="ck_behavior_claims_confidence"),
        CheckConstraint(
            "jsonb_typeof(dependency_path) = 'array'",
            name="ck_behavior_claims_dependency_array",
        ),
        Index("ix_behavior_claims_tenant_package", "tenant_id", "package_id"),
    )
