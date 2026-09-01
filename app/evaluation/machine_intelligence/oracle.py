"""Machine-readable oracle for the conveyor-line fixture.

Object names follow the SIN-89 engineering model. Artifact paths stand in for
document_id until SIN-100 ingests the package. Every canonical fact carries
an evidence locator with the fields that locator kind requires (minus the
runtime document id).
"""

from __future__ import annotations

from app.evaluation.machine_intelligence.dialect import dialect_document
from app.evaluation.machine_intelligence.line import (
    CPU_TYPE,
    DATASET_NAME,
    MACHINE_NAME,
    PACKAGE_SLUG,
    Evidence,
    Line,
    build_line,
    ci_subset,
    io_by_name,
)
from app.evaluation.machine_intelligence.tables import fb_ctrl_scl, line_containing, ob1_scl

# Artifact path stands in for document_id. Field set matches SIN-89 locators.
ORACLE_LOCATOR_REQUIREMENTS = {
    "chunk": ("source_id",),
    "page": ("page_number",),
    "sheet_cell": ("sheet_name", "cell_range"),
    "image_region": ("region",),
    "xml_path": ("xml_path",),
    "line_range": ("line_start",),
    "native_id": ("native_object_id",),
}


def locator_is_complete(evidence: dict) -> bool:
    kind = evidence.get("locator_kind")
    required = ORACLE_LOCATOR_REQUIREMENTS.get(kind)
    if not required:
        return False
    if not evidence.get("artifact"):
        return False
    for field in required:
        value = evidence.get(field)
        if field == "region":
            if not value:
                return False
            continue
        if value is None:
            return False
    return True


def _fact(id_: str, **body) -> dict:
    return {"id": id_, **body}


def build_oracle(line: Line | None = None) -> dict:
    line = line or build_line()
    entities = _entities(line)
    relations = _relations(line)
    documents = _documents()
    plc_variables = _plc_variables(line)
    behavior = [
        {
            "id": chain["id"],
            "claim_kind": chain["kind"],
            "dependency_path": chain["path"],
            "evidence": chain["evidence"],
        }
        for chain in line.behavior_chains
    ]
    subset = ci_subset(line)
    subset["entity_ids"] = [item["id"] for item in entities if _entity_in_ci(item["id"], subset)]
    subset["document_ids"] = [item["id"] for item in documents]
    return {
        "dataset": DATASET_NAME,
        "track": "machine_intelligence",
        "generator_version": line.generator_version,
        "dialect": dialect_document(),
        "package": {"slug": PACKAGE_SLUG, "name": MACHINE_NAME},
        "machine": {
            "code": line.machine_code,
            "name": line.machine_name,
            "revision": "A",
            "attributes": {"cpu_class": CPU_TYPE},
        },
        "assemblies": line.zones,
        "documents": documents,
        "entities": entities,
        "relations": relations,
        "plc_programs": [
            {
                "id": "prg-cl12",
                "name": "CL12_CPU",
                "dialect": "tia_s7",
                "attributes": {"cpu_class": CPU_TYPE},
                "evidence": Evidence(
                    locator_kind="xml_path",
                    artifact="plc/OB1.xml",
                    xml_path="/Document/Engineering",
                ).as_dict(),
            }
        ],
        "plc_blocks": [
            {
                "id": block["name"],
                "name": block["name"],
                "block_type": block["block_type"],
                "language": block["language"],
                "program_id": "prg-cl12",
                "attributes": {
                    key: block[key] for key in ("role", "instance_of", "instances") if key in block
                },
                "evidence": block["evidence"],
            }
            for block in line.blocks
        ],
        "plc_variables": plc_variables,
        "plc_references": _plc_references(line),
        "conflicts": line.conflicts,
        "unsupported_constructs": line.unsupported,
        "behavior_claims": behavior,
        "scenarios": line.scenarios,
        "mutations": line.mutations,
        "manufacturer_facts": line.manufacturer_facts,
        "profiles": {"ci": subset, "full": {"includes": "all"}},
    }


def _entity_in_ci(entity_id: str, subset: dict) -> bool:
    io_names = set(subset["io_names"])
    component_ids = set(subset["component_ids"])
    conveyor_ids = set(subset["conveyor_ids"])
    numbers = {int(code[2:]) for code in conveyor_ids}
    if entity_id in io_names or entity_id in component_ids:
        return True
    if entity_id.startswith("W-"):
        parts = entity_id.split("-")
        return len(parts) >= 2 and parts[1] in conveyor_ids
    if entity_id.startswith("X") and ":" in entity_id:
        number = int(entity_id.split(":")[1].split(".")[0])
        return number in numbers
    return False


def _doc(
    id_: str,
    relative_path: str,
    role: str,
    fmt: str,
    source: str | None = None,
) -> dict:
    item = {"id": id_, "relative_path": relative_path, "role": role, "format": fmt}
    if source is not None:
        item["source"] = source
    return item


def _documents() -> list[dict]:
    return [
        _doc("overview", "overview.md", "overview", "md"),
        _doc("schematic", "schematic.pdf", "schematic", "pdf", "schematic.txt"),
        _doc("io-list", "io_list.xlsx", "io", "xlsx", "io_list.tsv"),
        _doc("hardware", "hardware.xlsx", "hardware", "xlsx", "hardware.tsv"),
        _doc("cables", "cables.xlsx", "cables", "xlsx", "cables.tsv"),
        _doc("terminals", "terminals.xlsx", "terminals", "xlsx", "terminals.tsv"),
        _doc("alarms", "alarms.xlsx", "alarms", "xlsx", "alarms.tsv"),
        _doc("bom", "bom.xlsx", "bom", "xlsx", "bom.tsv"),
        _doc("bom-old", "bom_rev_old.xlsx", "revised", "xlsx", "bom_rev_old.tsv"),
        _doc("motor-drive", "motor_drive.xlsx", "motor_drive", "xlsx", "motor_drive.tsv"),
        _doc("commissioning", "commissioning.md", "commissioning", "md"),
        _doc(
            "manufacturer-facts",
            "manufacturer_facts.json",
            "manufacturer_facts",
            "json",
        ),
        _doc("revision", "revision.md", "revision", "md"),
        _doc("unrelated", "unrelated_hvac.md", "unrelated", "md"),
        _doc("photo", "cabinet_photo.png", "photo", "png"),
        _doc("ob1-xml", "plc/OB1.xml", "plc_xml", "xml"),
        _doc("ob1-scl", "plc/OB1.scl", "plc_scl", "scl"),
        _doc("fb-ctrl-xml", "plc/FB_ConveyorCtrl.xml", "plc_xml", "xml"),
        _doc("fb-ctrl-scl", "plc/FB_ConveyorCtrl.scl", "plc_scl", "scl"),
        _doc("fb-alarm-xml", "plc/FB_Alarm.xml", "plc_xml", "xml"),
        _doc("fb-alarm-scl", "plc/FB_Alarm.scl", "plc_scl", "scl"),
        _doc("fc-mode-xml", "plc/FC_Mode.xml", "plc_xml", "xml"),
        _doc("fc-mode-scl", "plc/FC_Mode.scl", "plc_scl", "scl"),
        _doc("db-line-xml", "plc/DB_Line.xml", "plc_xml", "xml"),
        _doc("xref", "plc/cross_references.csv", "cross_references", "csv"),
        _doc("s5", "s5/conveyor_start.awl", "s5_text", "awl"),
    ]


def _entities(line: Line) -> list[dict]:
    entities = []
    for point in line.io_points:
        entities.append(
            _fact(
                point.name,
                entity_kind="signal",
                canonical_name=point.name,
                assembly_id=next(
                    (c.zone_id for c in line.conveyors if c.code == point.conveyor_id),
                    "zone-a",
                ),
                attributes={
                    "address": point.address,
                    "data_type": point.data_type,
                    "io_direction": point.direction,
                    "comment": point.comment,
                },
                evidence=[point.evidence.as_dict()],
            )
        )
    for item in line.components + line.cables + line.terminals:
        entities.append(
            _fact(
                item["id"],
                entity_kind=item["kind"],
                canonical_name=item["name"],
                assembly_id=item.get("assembly_id"),
                attributes=item.get("attributes") or {},
                evidence=[item["evidence"]],
            )
        )
    return entities


def _relations(line: Line) -> list[dict]:
    return [
        _fact(
            item["id"],
            relation_kind=item["kind"],
            source_entity_id=item["source"],
            target_entity_id=item["target"],
            evidence=[item["evidence"]],
        )
        for item in line.connections
    ]


def _plc_variables(line: Line) -> list[dict]:
    variables = []
    for point in line.io_points:
        variables.append(
            {
                "id": point.name,
                "name": point.name,
                "program_id": "prg-cl12",
                "data_type": point.data_type,
                "address": point.address,
                "io_direction": point.direction,
                "entity_id": point.name,
                "evidence": [point.evidence.as_dict()],
            }
        )
    return variables


def _plc_references(line: Line) -> list[dict]:
    fc_mode_line = line_containing(ob1_scl(), "FC_Mode();")
    start_line = line_containing(fb_ctrl_scl(), "RunCmd :=")
    refs = [
        {
            "id": "ref-ob1-fc-mode",
            "block_id": "OB1",
            "variable_id": None,
            "reference_kind": "call",
            "network_ordinal": 1,
            "original_construct": "FC_Mode",
            "evidence": [
                Evidence(
                    locator_kind="line_range",
                    artifact="plc/OB1.scl",
                    line_start=fc_mode_line,
                    line_end=fc_mode_line,
                ).as_dict()
            ],
        }
    ]
    for conveyor in line.conveyors:
        run = io_by_name(line, f"{conveyor.code}.RunCmd")
        refs.append(
            {
                "id": f"ref-{conveyor.code}-run",
                "block_id": "FB_ConveyorCtrl",
                "variable_id": run.name,
                "reference_kind": "write",
                "network_ordinal": 1,
                "original_construct": "assignment",
                "evidence": [
                    Evidence(
                        locator_kind="xml_path",
                        artifact="plc/FB_ConveyorCtrl.xml",
                        xml_path="/Document/SW.Blocks.FB/ObjectList/SW.Blocks.CompileUnit[@ID='1']",
                    ).as_dict()
                ],
            }
        )
        start = io_by_name(line, f"{conveyor.code}.Start")
        refs.append(
            {
                "id": f"ref-{conveyor.code}-start",
                "block_id": "FB_ConveyorCtrl",
                "variable_id": start.name,
                "reference_kind": "read",
                "network_ordinal": 1,
                "original_construct": "condition",
                "evidence": [
                    Evidence(
                        locator_kind="line_range",
                        artifact="plc/FB_ConveyorCtrl.scl",
                        line_start=start_line,
                        line_end=start_line,
                    ).as_dict()
                ],
            }
        )
    return refs


def canonical_facts(oracle: dict) -> list[dict]:
    """Facts that SIN-89 would persist as canonical rows."""
    facts: list[dict] = []
    for key in (
        "entities",
        "relations",
        "plc_programs",
        "plc_blocks",
        "plc_variables",
        "plc_references",
        "conflicts",
        "unsupported_constructs",
        "behavior_claims",
    ):
        for item in oracle[key]:
            facts.append({"collection": key, **item})
    return facts


def facts_missing_evidence(oracle: dict) -> list[str]:
    missing = []
    for fact in canonical_facts(oracle):
        evidence = fact.get("evidence")
        locators = evidence if isinstance(evidence, list) else [evidence] if evidence else []
        if not locators:
            missing.append(f"{fact['collection']}:{fact['id']}")
            continue
        for locator in locators:
            if not locator_is_complete(locator):
                missing.append(f"{fact['collection']}:{fact['id']}")
                break
    return missing


SHEET_SOURCES = {
    "io_list.xlsx": "sources/io_list.tsv",
    "bom.xlsx": "sources/bom.tsv",
    "bom_rev_old.xlsx": "sources/bom_rev_old.tsv",
    "hardware.xlsx": "sources/hardware.tsv",
    "cables.xlsx": "sources/cables.tsv",
    "terminals.xlsx": "sources/terminals.tsv",
    "alarms.xlsx": "sources/alarms.tsv",
    "motor_drive.xlsx": "sources/motor_drive.tsv",
}

LINE_SOURCES = {
    "plc/OB1.scl": "sources/plc/OB1.scl",
    "plc/FB_ConveyorCtrl.scl": "sources/plc/FB_ConveyorCtrl.scl",
    "plc/FB_Alarm.scl": "sources/plc/FB_Alarm.scl",
    "plc/FC_Mode.scl": "sources/plc/FC_Mode.scl",
}


def _cell_row(cell_range: str) -> int:
    digits = "".join(ch for ch in cell_range if ch.isdigit())
    if not digits:
        raise ValueError(cell_range)
    return int(digits)


def evidence_resolution_errors(
    oracle: dict,
    sources: dict[str, str],
    extras: list[dict] | None = None,
) -> list[str]:
    """Locators must resolve inside the generated sources, not merely exist."""
    errors: list[str] = []
    facts = canonical_facts(oracle)
    for extra in extras or []:
        facts.append({"collection": "extra", **extra})
    for fact in facts:
        locators = fact.get("evidence")
        if not locators:
            continue
        if not isinstance(locators, list):
            locators = [locators]
        expected = str(fact["id"])
        for locator in locators:
            kind = locator.get("locator_kind")
            artifact = locator.get("artifact")
            if kind == "sheet_cell":
                source_path = SHEET_SOURCES.get(artifact)
                if source_path is None:
                    errors.append(f"{fact['collection']}:{expected} unknown sheet {artifact}")
                    continue
                rows = sources[source_path].splitlines()
                row_number = _cell_row(locator["cell_range"])
                if row_number < 1 or row_number > len(rows):
                    errors.append(
                        f"{fact['collection']}:{expected} {artifact} {locator['cell_range']} "
                        f"outside {len(rows)} rows"
                    )
                    continue
                cells = rows[row_number - 1].split("\t")
                if (
                    fact["collection"] in {"entities", "plc_variables", "extra"}
                    and expected not in cells
                ):
                    errors.append(
                        f"{fact['collection']}:{expected} {artifact} "
                        f"{locator['cell_range']} holds {cells[0]!r}"
                    )
            elif kind == "line_range":
                source_path = LINE_SOURCES.get(artifact)
                if source_path is None:
                    errors.append(f"{fact['collection']}:{expected} unknown listing {artifact}")
                    continue
                lines = sources[source_path].splitlines()
                start = int(locator["line_start"])
                end = int(locator.get("line_end") or start)
                if start < 1 or end > len(lines) or end < start:
                    errors.append(
                        f"{fact['collection']}:{expected} {artifact}:{start}-{end} "
                        f"outside {len(lines)} lines"
                    )
    return errors
