"""Tenant-scoped persistence for the machine/engineering evidence model.

Every function takes `tenant_id` as an explicit argument. Canonical entities
and relations require at least one evidence locator in the same tenant.
Human overrides never delete the row they correct.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engineering_models import (
    Assembly,
    AssignmentState,
    BehaviorClaimKind,
    ConflictKind,
    ConflictStatus,
    DerivedBehaviorClaim,
    EngineeringConflict,
    EngineeringEntity,
    EngineeringEntityCandidate,
    EngineeringRelation,
    EngineeringRelationCandidate,
    EngineeringRelationKind,
    EntityKind,
    EvidenceBinding,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSubjectKind,
    HumanOverride,
    IdentityStatus,
    Machine,
    MachinePackage,
    PackageAssignment,
    PackageDocument,
    PackageStatus,
    PlcBlock,
    PlcDialect,
    PlcProgram,
    PlcReference,
    PlcReferenceKind,
    PlcVariable,
    UnsupportedConstruct,
)

_METHOD = "manual"
_METHOD_VERSION = "sin-89"


class EngineeringEvidenceError(ValueError):
    """A canonical fact was submitted without resolvable evidence."""


_LOCATOR_REQUIREMENTS: dict[EvidenceLocatorKind, tuple[str, ...]] = {
    EvidenceLocatorKind.chunk: ("document_id", "source_id"),
    EvidenceLocatorKind.page: ("document_id", "page_number"),
    EvidenceLocatorKind.sheet_cell: ("document_id", "sheet_name", "cell_range"),
    EvidenceLocatorKind.image_region: ("document_id", "region"),
    EvidenceLocatorKind.xml_path: ("document_id", "xml_path"),
    EvidenceLocatorKind.line_range: ("document_id", "line_start"),
    EvidenceLocatorKind.native_id: ("document_id", "native_object_id"),
}


def _locator_is_complete(
    *,
    locator_kind: EvidenceLocatorKind,
    document_id,
    source_id,
    page_number,
    sheet_name,
    cell_range,
    xml_path,
    line_start,
    native_object_id,
    region: dict | None,
) -> bool:
    values = {
        "document_id": document_id,
        "source_id": source_id,
        "page_number": page_number,
        "sheet_name": sheet_name,
        "cell_range": cell_range,
        "xml_path": xml_path,
        "line_start": line_start,
        "native_object_id": native_object_id,
        "region": region if region else None,
    }
    return all(values[field] is not None for field in _LOCATOR_REQUIREMENTS[locator_kind])


class EngineeringIsolationError(ValueError):
    """A referenced row is missing inside this tenant."""


async def _package_or_raise(session: AsyncSession, *, tenant_id, package_id) -> MachinePackage:
    package = await get_package(session, tenant_id=tenant_id, package_id=package_id)
    if package is None:
        raise EngineeringIsolationError("package not found")
    return package


async def _evidence_in_tenant(
    session: AsyncSession, *, tenant_id, evidence_ids: list
) -> list[EvidenceReference]:
    if not evidence_ids:
        raise EngineeringEvidenceError("canonical facts require evidence")
    result = await session.execute(
        select(EvidenceReference).where(
            EvidenceReference.tenant_id == tenant_id,
            EvidenceReference.id.in_(evidence_ids),
        )
    )
    rows = list(result.scalars().all())
    if len(rows) != len(set(evidence_ids)):
        raise EngineeringIsolationError("evidence not found")
    return rows


async def _bind(
    session: AsyncSession,
    *,
    tenant_id,
    evidence_ids: list,
    subject_kind: EvidenceSubjectKind,
    subject_id,
) -> None:
    rows = await _evidence_in_tenant(session, tenant_id=tenant_id, evidence_ids=evidence_ids)
    for evidence in rows:
        session.add(
            EvidenceBinding(
                tenant_id=tenant_id,
                evidence_id=evidence.id,
                subject_kind=subject_kind,
                subject_id=subject_id,
            )
        )


async def create_package(
    session: AsyncSession, *, tenant_id, slug: str, name: str
) -> MachinePackage:
    package = MachinePackage(
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        status=PackageStatus.draft,
    )
    session.add(package)
    await session.flush()
    return package


async def get_package(session: AsyncSession, *, tenant_id, package_id) -> MachinePackage | None:
    result = await session.execute(
        select(MachinePackage).where(
            MachinePackage.id == package_id, MachinePackage.tenant_id == tenant_id
        )
    )
    return result.scalars().first()


async def list_packages(session: AsyncSession, *, tenant_id) -> list[MachinePackage]:
    result = await session.execute(
        select(MachinePackage)
        .where(MachinePackage.tenant_id == tenant_id)
        .order_by(MachinePackage.created_at.desc())
    )
    return list(result.scalars().all())


async def add_package_document(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    document_id,
    relative_path: str | None = None,
) -> PackageDocument:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    membership = PackageDocument(
        tenant_id=tenant_id,
        package_id=package_id,
        document_id=document_id,
        relative_path=relative_path,
    )
    session.add(membership)
    await session.flush()
    return membership


async def list_package_documents(
    session: AsyncSession, *, tenant_id, package_id
) -> list[PackageDocument]:
    result = await session.execute(
        select(PackageDocument).where(
            PackageDocument.tenant_id == tenant_id,
            PackageDocument.package_id == package_id,
        )
    )
    return list(result.scalars().all())


async def create_machine(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    name: str,
    code: str | None = None,
    revision: str | None = None,
) -> Machine:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    machine = Machine(
        tenant_id=tenant_id,
        package_id=package_id,
        name=name,
        code=code,
        revision=revision,
        identity_status=IdentityStatus.unresolved,
    )
    session.add(machine)
    await session.flush()
    return machine


async def get_machine(session: AsyncSession, *, tenant_id, machine_id) -> Machine | None:
    result = await session.execute(
        select(Machine).where(Machine.id == machine_id, Machine.tenant_id == tenant_id)
    )
    return result.scalars().first()


async def create_assembly(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    machine_id,
    name: str,
    code: str | None = None,
    parent_assembly_id=None,
) -> Assembly:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    if await get_machine(session, tenant_id=tenant_id, machine_id=machine_id) is None:
        raise EngineeringIsolationError("machine not found")
    assembly = Assembly(
        tenant_id=tenant_id,
        package_id=package_id,
        machine_id=machine_id,
        parent_assembly_id=parent_assembly_id,
        name=name,
        code=code,
    )
    session.add(assembly)
    await session.flush()
    return assembly


async def record_evidence(
    session: AsyncSession,
    *,
    tenant_id,
    locator_kind: EvidenceLocatorKind,
    document_id=None,
    source_id: str | None = None,
    page_number: int | None = None,
    sheet_name: str | None = None,
    cell_range: str | None = None,
    xml_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    native_object_id: str | None = None,
    region: dict | None = None,
) -> EvidenceReference:
    if not _locator_is_complete(
        locator_kind=locator_kind,
        document_id=document_id,
        source_id=source_id,
        page_number=page_number,
        sheet_name=sheet_name,
        cell_range=cell_range,
        xml_path=xml_path,
        line_start=line_start,
        native_object_id=native_object_id,
        region=region,
    ):
        raise EngineeringEvidenceError("evidence locator is incomplete")
    evidence = EvidenceReference(
        tenant_id=tenant_id,
        locator_kind=locator_kind,
        document_id=document_id,
        source_id=source_id,
        page_number=page_number,
        sheet_name=sheet_name,
        cell_range=cell_range,
        xml_path=xml_path,
        line_start=line_start,
        line_end=line_end,
        native_object_id=native_object_id,
        region=region or {},
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def create_assignment(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    document_id,
    evidence_ids: list,
    machine_id=None,
    assembly_id=None,
    state: AssignmentState = AssignmentState.proposed,
    confidence: float = 0.5,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
) -> PackageAssignment:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    assignment = PackageAssignment(
        tenant_id=tenant_id,
        package_id=package_id,
        document_id=document_id,
        machine_id=machine_id,
        assembly_id=assembly_id,
        state=state,
        confidence=confidence,
        method=method,
        method_version=method_version,
    )
    session.add(assignment)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.package_assignment,
        subject_id=assignment.id,
    )
    await session.flush()
    return assignment


async def create_entity_candidate(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    entity_kind: EntityKind,
    proposed_name: str,
    evidence_ids: list,
    aliases: list | None = None,
    confidence: float = 0.5,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
    attributes: dict | None = None,
) -> EngineeringEntityCandidate:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    candidate = EngineeringEntityCandidate(
        tenant_id=tenant_id,
        package_id=package_id,
        entity_kind=entity_kind,
        proposed_name=proposed_name,
        aliases=aliases or [],
        identity_status=IdentityStatus.unresolved,
        confidence=confidence,
        method=method,
        method_version=method_version,
        attributes=attributes or {},
    )
    session.add(candidate)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.entity_candidate,
        subject_id=candidate.id,
    )
    await session.flush()
    return candidate


async def create_canonical_entity(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    entity_kind: EntityKind,
    canonical_name: str,
    evidence_ids: list,
    machine_id=None,
    assembly_id=None,
    aliases: list | None = None,
    identity_status: IdentityStatus = IdentityStatus.resolved,
    confidence: float = 1.0,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
    attributes: dict | None = None,
) -> EngineeringEntity:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    entity = EngineeringEntity(
        tenant_id=tenant_id,
        package_id=package_id,
        machine_id=machine_id,
        assembly_id=assembly_id,
        entity_kind=entity_kind,
        canonical_name=canonical_name,
        aliases=aliases or [],
        identity_status=identity_status,
        confidence=confidence,
        method=method,
        method_version=method_version,
        attributes=attributes or {},
    )
    session.add(entity)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.entity,
        subject_id=entity.id,
    )
    await session.flush()
    return entity


async def get_entity(
    session: AsyncSession, *, tenant_id, entity_id, package_id=None
) -> EngineeringEntity | None:
    clauses = [
        EngineeringEntity.id == entity_id,
        EngineeringEntity.tenant_id == tenant_id,
    ]
    if package_id is not None:
        clauses.append(EngineeringEntity.package_id == package_id)
    result = await session.execute(select(EngineeringEntity).where(*clauses))
    return result.scalars().first()


async def list_entities(session: AsyncSession, *, tenant_id, package_id) -> list[EngineeringEntity]:
    result = await session.execute(
        select(EngineeringEntity).where(
            EngineeringEntity.tenant_id == tenant_id,
            EngineeringEntity.package_id == package_id,
        )
    )
    return list(result.scalars().all())


async def create_canonical_relation(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    relation_kind: EngineeringRelationKind,
    source_entity_id,
    target_entity_id,
    evidence_ids: list,
    confidence: float = 1.0,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
    attributes: dict | None = None,
) -> EngineeringRelation:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    if (
        await get_entity(
            session, tenant_id=tenant_id, entity_id=source_entity_id, package_id=package_id
        )
        is None
    ):
        raise EngineeringIsolationError("source entity not found")
    if (
        await get_entity(
            session, tenant_id=tenant_id, entity_id=target_entity_id, package_id=package_id
        )
        is None
    ):
        raise EngineeringIsolationError("target entity not found")
    relation = EngineeringRelation(
        tenant_id=tenant_id,
        package_id=package_id,
        relation_kind=relation_kind,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        identity_status=IdentityStatus.resolved,
        confidence=confidence,
        method=method,
        method_version=method_version,
        attributes=attributes or {},
    )
    session.add(relation)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.relation,
        subject_id=relation.id,
    )
    await session.flush()
    return relation


async def get_relation(
    session: AsyncSession, *, tenant_id, relation_id
) -> EngineeringRelation | None:
    result = await session.execute(
        select(EngineeringRelation).where(
            EngineeringRelation.id == relation_id, EngineeringRelation.tenant_id == tenant_id
        )
    )
    return result.scalars().first()


async def create_relation_candidate(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    relation_kind: EngineeringRelationKind,
    evidence_ids: list,
    source_entity_id=None,
    target_entity_id=None,
    confidence: float = 0.5,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
    attributes: dict | None = None,
) -> EngineeringRelationCandidate:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    if source_entity_id is not None and (
        await get_entity(
            session, tenant_id=tenant_id, entity_id=source_entity_id, package_id=package_id
        )
        is None
    ):
        raise EngineeringIsolationError("source entity not found")
    if target_entity_id is not None and (
        await get_entity(
            session, tenant_id=tenant_id, entity_id=target_entity_id, package_id=package_id
        )
        is None
    ):
        raise EngineeringIsolationError("target entity not found")
    candidate = EngineeringRelationCandidate(
        tenant_id=tenant_id,
        package_id=package_id,
        relation_kind=relation_kind,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        identity_status=IdentityStatus.unresolved,
        confidence=confidence,
        method=method,
        method_version=method_version,
        attributes=attributes or {},
    )
    session.add(candidate)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.relation_candidate,
        subject_id=candidate.id,
    )
    await session.flush()
    return candidate


async def list_evidence_for(
    session: AsyncSession, *, tenant_id, subject_kind: EvidenceSubjectKind, subject_id
) -> list[EvidenceReference]:
    result = await session.execute(
        select(EvidenceReference)
        .join(
            EvidenceBinding,
            (EvidenceBinding.evidence_id == EvidenceReference.id)
            & (EvidenceBinding.tenant_id == EvidenceReference.tenant_id),
        )
        .where(
            EvidenceBinding.tenant_id == tenant_id,
            EvidenceBinding.subject_kind == subject_kind,
            EvidenceBinding.subject_id == subject_id,
        )
    )
    return list(result.scalars().all())


async def create_plc_program(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    name: str,
    dialect: PlcDialect = PlcDialect.tia_s7,
    machine_id=None,
) -> PlcProgram:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    program = PlcProgram(
        tenant_id=tenant_id,
        package_id=package_id,
        machine_id=machine_id,
        name=name,
        dialect=dialect,
    )
    session.add(program)
    await session.flush()
    return program


async def create_plc_block(
    session: AsyncSession, *, tenant_id, program_id, name: str, block_type: str
) -> PlcBlock:
    result = await session.execute(
        select(PlcProgram).where(PlcProgram.id == program_id, PlcProgram.tenant_id == tenant_id)
    )
    if result.scalars().first() is None:
        raise EngineeringIsolationError("program not found")
    block = PlcBlock(
        tenant_id=tenant_id,
        program_id=program_id,
        name=name,
        block_type=block_type,
    )
    session.add(block)
    await session.flush()
    return block


async def create_plc_variable(
    session: AsyncSession,
    *,
    tenant_id,
    program_id,
    name: str,
    block_id=None,
    entity_id=None,
    address: str | None = None,
    data_type: str | None = None,
    io_direction: str | None = None,
) -> PlcVariable:
    result = await session.execute(
        select(PlcProgram).where(PlcProgram.id == program_id, PlcProgram.tenant_id == tenant_id)
    )
    if result.scalars().first() is None:
        raise EngineeringIsolationError("program not found")
    variable = PlcVariable(
        tenant_id=tenant_id,
        program_id=program_id,
        block_id=block_id,
        entity_id=entity_id,
        name=name,
        address=address,
        data_type=data_type,
        io_direction=io_direction,
    )
    session.add(variable)
    await session.flush()
    return variable


async def create_plc_reference(
    session: AsyncSession,
    *,
    tenant_id,
    block_id,
    reference_kind: PlcReferenceKind,
    variable_id=None,
    network_ordinal: int | None = None,
    original_construct: str | None = None,
) -> PlcReference:
    result = await session.execute(
        select(PlcBlock).where(PlcBlock.id == block_id, PlcBlock.tenant_id == tenant_id)
    )
    if result.scalars().first() is None:
        raise EngineeringIsolationError("block not found")
    reference = PlcReference(
        tenant_id=tenant_id,
        block_id=block_id,
        variable_id=variable_id,
        reference_kind=reference_kind,
        network_ordinal=network_ordinal,
        original_construct=original_construct,
    )
    session.add(reference)
    await session.flush()
    return reference


async def record_conflict(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    conflict_kind: ConflictKind,
    left_subject_kind: str,
    left_subject_id,
    right_subject_kind: str,
    right_subject_id,
    evidence_ids: list,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
) -> EngineeringConflict:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    conflict = EngineeringConflict(
        tenant_id=tenant_id,
        package_id=package_id,
        conflict_kind=conflict_kind,
        status=ConflictStatus.open,
        left_subject_kind=left_subject_kind,
        left_subject_id=left_subject_id,
        right_subject_kind=right_subject_kind,
        right_subject_id=right_subject_id,
        method=method,
        method_version=method_version,
    )
    session.add(conflict)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.conflict,
        subject_id=conflict.id,
    )
    await session.flush()
    return conflict


async def record_unsupported_construct(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    construct_code: str,
    evidence_ids: list,
    document_id=None,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
) -> UnsupportedConstruct:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    row = UnsupportedConstruct(
        tenant_id=tenant_id,
        package_id=package_id,
        document_id=document_id,
        construct_code=construct_code,
        method=method,
        method_version=method_version,
    )
    session.add(row)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.unsupported_construct,
        subject_id=row.id,
    )
    await session.flush()
    return row


async def record_override(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    subject_kind: EvidenceSubjectKind,
    subject_id,
    actor_id: str,
    previous_payload: dict,
    new_payload: dict,
) -> HumanOverride:
    """Record a correction. Callers must leave the original prediction row in place."""
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    override = HumanOverride(
        tenant_id=tenant_id,
        package_id=package_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        actor_id=actor_id,
        previous_payload=previous_payload,
        new_payload=new_payload,
    )
    session.add(override)
    await session.flush()
    return override


async def record_behavior_claim(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    claim_kind: BehaviorClaimKind,
    dependency_path: list,
    evidence_ids: list,
    confidence: float = 1.0,
    method: str = _METHOD,
    method_version: str = _METHOD_VERSION,
) -> DerivedBehaviorClaim:
    await _package_or_raise(session, tenant_id=tenant_id, package_id=package_id)
    if not dependency_path:
        raise EngineeringEvidenceError("behavior claims require a dependency path")
    claim = DerivedBehaviorClaim(
        tenant_id=tenant_id,
        package_id=package_id,
        claim_kind=claim_kind,
        identity_status=IdentityStatus.resolved,
        confidence=confidence,
        method=method,
        method_version=method_version,
        dependency_path=dependency_path,
    )
    session.add(claim)
    await session.flush()
    await _bind(
        session,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        subject_kind=EvidenceSubjectKind.behavior_claim,
        subject_id=claim.id,
    )
    await session.flush()
    return claim


_GROUNDED_SUBJECTS = (
    (EvidenceSubjectKind.relation, EngineeringRelation),
    (EvidenceSubjectKind.relation_candidate, EngineeringRelationCandidate),
    (EvidenceSubjectKind.behavior_claim, DerivedBehaviorClaim),
    (EvidenceSubjectKind.conflict, EngineeringConflict),
    (EvidenceSubjectKind.unsupported_construct, UnsupportedConstruct),
    (EvidenceSubjectKind.entity_candidate, EngineeringEntityCandidate),
    (EvidenceSubjectKind.package_assignment, PackageAssignment),
    (EvidenceSubjectKind.entity, EngineeringEntity),
)


async def drop_document_evidence(session: AsyncSession, *, tenant_id, document_id) -> None:
    """Remove locators for one document and drop subjects that lose their last evidence."""
    affected = (
        await session.execute(
            select(EvidenceBinding.subject_kind, EvidenceBinding.subject_id).where(
                EvidenceBinding.tenant_id == tenant_id,
                EvidenceBinding.evidence_id.in_(
                    select(EvidenceReference.id).where(
                        EvidenceReference.tenant_id == tenant_id,
                        EvidenceReference.document_id == document_id,
                    )
                ),
            )
        )
    ).all()
    await session.execute(
        delete(EvidenceReference).where(
            EvidenceReference.tenant_id == tenant_id,
            EvidenceReference.document_id == document_id,
        )
    )
    await session.flush()
    remaining_by_kind: dict[EvidenceSubjectKind, set] = {}
    for kind, subject_id in affected:
        remaining_by_kind.setdefault(kind, set()).add(subject_id)
    for kind, model in _GROUNDED_SUBJECTS:
        ids = remaining_by_kind.get(kind)
        if not ids:
            continue
        still_bound = set(
            (
                await session.execute(
                    select(EvidenceBinding.subject_id).where(
                        EvidenceBinding.tenant_id == tenant_id,
                        EvidenceBinding.subject_kind == kind,
                        EvidenceBinding.subject_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        unbound = ids - still_bound
        if unbound:
            await session.execute(
                delete(model).where(model.tenant_id == tenant_id, model.id.in_(unbound))
            )
    await session.flush()
