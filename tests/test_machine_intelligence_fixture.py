"""SIN-99 conveyor-line fixture, oracle and SimaticML dialect contract."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.evaluation.formats import build_file
from app.evaluation.machine_intelligence.__main__ import _check
from app.evaluation.machine_intelligence.artifacts import (
    DATASET_ROOT,
    binary_files,
    build_multipage_pdf,
    generated_files,
    source_texts,
    write_dataset,
)
from app.evaluation.machine_intelligence.compare import profile_is_subset, score_ids
from app.evaluation.machine_intelligence.dialect import REQUIRED_ELEMENTS, SYNTHETIC_XML_BANNER
from app.evaluation.machine_intelligence.line import (
    CI_PAGE_MAX,
    GENERATOR_VERSION,
    build_line,
    ci_subset,
    page_number,
)
from app.evaluation.machine_intelligence.mutations import apply_mutation
from app.evaluation.machine_intelligence.oracle import (
    build_oracle,
    evidence_resolution_errors,
    facts_missing_evidence,
)
from app.evaluation.machine_intelligence.tables import first_column_row, line_containing

SCENARIO_IDS = {
    "startup-auto",
    "downstream-blocked",
    "jam-missing-speed",
    "vfd-fault-cv01",
    "protect-fb-mismatch",
    "sensor-implausible",
    "reset-restart",
}


def test_line_meets_scale_gates():
    line = build_line()
    assert line.generator_version == GENERATOR_VERSION
    assert len(line.conveyors) == 12
    assert len(line.io_points) >= 150
    assert 25 <= len(line.schematic_pages) <= 40
    assert len(line.schematic_pages) == 30
    assert {c.drive for c in line.conveyors} >= {"vfd", "dol"}
    assert any(c.reversing for c in line.conveyors)
    instances = next(b for b in line.blocks if b["name"] == "FB_ConveyorCtrl")["instances"]
    assert len(instances) == 12
    assert len(line.behavior_chains) >= 3
    assert {chain["id"] for chain in line.behavior_chains} >= {
        "chain-auto-start-zone-a",
        "chain-downstream-inhibit",
        "chain-jam-stop",
    }
    assert {item["id"] for item in line.scenarios} == SCENARIO_IDS
    assert any(item["attributes"].get("class") == "S7-1500-class" for item in line.hardware)
    assert any(item["attributes"].get("role") == "distributed_io" for item in line.hardware)


def test_ci_subset_is_numeric_and_contained_in_full():
    line = build_line()
    subset = ci_subset(line)
    assert subset["generator_version"] == line.generator_version
    assert subset["conveyor_ids"] == ["CV01", "CV02", "CV03"]
    page_ids = subset["page_ids"]
    assert "E-09" in page_ids
    assert "E-14" in page_ids
    assert "E-15" not in page_ids
    assert all(page_number(page_id) <= CI_PAGE_MAX for page_id in page_ids)
    assert set(subset["io_names"]) <= {point.name for point in line.io_points}
    assert set(subset["component_ids"]) <= {item["id"] for item in line.components}
    oracle = build_oracle(line)
    assert profile_is_subset(oracle) == []
    ci_ids = set(oracle["profiles"]["ci"]["entity_ids"])
    assert ci_ids <= {item["id"] for item in oracle["entities"]}
    assert "CV12.RunCmd" not in oracle["profiles"]["ci"]["entity_ids"]
    assert "CV01.RunCmd" in oracle["profiles"]["ci"]["entity_ids"]


def test_every_canonical_fact_has_complete_evidence():
    line = build_line()
    oracle = build_oracle(line)
    missing = facts_missing_evidence(oracle)
    assert missing == []
    assert evidence_resolution_errors(oracle, source_texts(line), extras=line.alarms) == []
    kinds: set[str] = set()
    collections = (
        oracle["entities"]
        + oracle["relations"]
        + oracle["plc_blocks"]
        + oracle["plc_variables"]
        + oracle["plc_references"]
        + oracle["conflicts"]
        + oracle["unsupported_constructs"]
        + oracle["behavior_claims"]
        + oracle["plc_programs"]
    )
    for fact in collections:
        locators = fact["evidence"]
        if not isinstance(locators, list):
            locators = [locators]
        kinds.update(locator["locator_kind"] for locator in locators)
    assert kinds >= {
        "page",
        "sheet_cell",
        "xml_path",
        "line_range",
        "image_region",
        "native_id",
    }


def test_identity_conflict_does_not_merge_reused_comments():
    line = build_line()
    pe01 = next(p for p in line.io_points if p.name == "CV01.PEInfeed")
    pe08 = next(p for p in line.io_points if p.name == "CV08.PEInfeed")
    assert pe01.comment == pe08.comment == "PE infeed"
    assert pe01.address != pe08.address
    conflict = next(item for item in line.conflicts if item["id"] == "conflict-reused-tag")
    assert conflict["left"] != conflict["right"]


def test_behavior_chains_cover_the_physical_path():
    oracle = build_oracle()
    claims = oracle["behavior_claims"]
    startup = next(item for item in claims if item["id"] == "chain-auto-start-zone-a")
    path = startup["dependency_path"]
    assert path[0] == "Line.AutoMode"
    assert "FC_Mode" in path
    assert "CV01.RunCmd" in path
    assert "CV01-M1" in path
    assert path[-1] == "CV01.PEDischarge"


def test_manufacturer_facts_are_synthetic_citations():
    facts = build_line().manufacturer_facts
    assert all(item["citation_url"].startswith("https://example.invalid/") for item in facts)
    assert any(item["component_id"] == "CV12-U1-wrong" for item in facts)
    assert "CV12-U1-wrong" not in {item["id"] for item in build_line().components}


def test_unsupported_construct_points_at_alarm_network_nine():
    item = build_line().unsupported[0]
    assert item["block"] == "FB_Alarm"
    assert item["network_ordinal"] == 9
    assert item["evidence"]["xml_path"].endswith("CompileUnit[@ID='9']")


def test_generated_xml_uses_dialect_elements_and_is_not_a_tia_export():
    texts = source_texts()
    fb = texts["sources/plc/FB_ConveyorCtrl.xml"]
    alarm = texts["sources/plc/FB_Alarm.xml"]
    assert SYNTHETIC_XML_BANNER in fb
    assert "NOT a TIA Portal export" in fb
    assert "produced by TIA" not in fb
    blob = "\n".join(body for path, body in texts.items() if path.endswith(".xml"))
    for element in REQUIRED_ELEMENTS:
        assert element in blob, element
    assert "UNKNOWN_INSTRUCTION();" in alarm
    assert 'CompileUnit ID="9"' in alarm
    assert "xmlns=" not in fb.split("\n", 2)[2]


def test_conformance_placeholder_is_not_a_fake_export(tmp_path: Path):
    write_dataset(tmp_path)
    conformance = tmp_path / "conformance"
    xml_files = list(conformance.glob("*.xml"))
    assert xml_files == []
    readme = (conformance / "README.md").read_text(encoding="utf-8")
    assert "not in this repository" in readme.lower()
    assert "sources/plc/" in readme
    provenance = json.loads((conformance / "provenance.template.json").read_text(encoding="utf-8"))
    assert provenance["status"] == "missing"
    assert provenance["expected_path"] == "conformance/sample.xml"
    assert "generator XML" in provenance["notes"]


def test_package_members_exist_in_sources():
    texts = source_texts()
    required = {
        "sources/overview.md",
        "sources/schematic.txt",
        "sources/io_list.tsv",
        "sources/hardware.tsv",
        "sources/cables.tsv",
        "sources/terminals.tsv",
        "sources/alarms.tsv",
        "sources/bom.tsv",
        "sources/bom_rev_old.tsv",
        "sources/motor_drive.tsv",
        "sources/commissioning.md",
        "sources/manufacturer_facts.json",
        "sources/revision.md",
        "sources/unrelated_hvac.md",
        "sources/plc/OB1.xml",
        "sources/plc/OB1.scl",
        "sources/plc/FB_ConveyorCtrl.xml",
        "sources/plc/FB_ConveyorCtrl.scl",
        "sources/plc/cross_references.csv",
        "sources/s5/conveyor_start.awl",
    }
    assert required <= set(texts)
    assert "RTU-9" in texts["sources/unrelated_hvac.md"]
    assert "CL-12" in texts["sources/overview.md"]
    pages = texts["sources/schematic.txt"].split("\f")
    assert len(pages) == 30
    assert "E-12" in texts["sources/schematic.txt"]
    assert "Cross-ref" in texts["sources/schematic.txt"]


def test_binaries_round_trip_sheet_names_and_page_count():
    files = binary_files()
    assert files["schematic.pdf"].startswith(b"%PDF-")
    assert files["schematic.pdf"].count(b"/Type /Page ") == 30
    assert files["cabinet_photo.png"].startswith(b"\x89PNG")
    workbook = zipfile.ZipFile(BytesIO(files["io_list.xlsx"])).read("xl/workbook.xml")
    assert b'name="IO"' in workbook
    drives = zipfile.ZipFile(BytesIO(files["motor_drive.xlsx"])).read("xl/workbook.xml")
    assert b'name="Drives"' in drives
    default, _mime = build_file("xlsx", "tag\tqty\nM1\t1\n")
    assert b'name="BOM"' in zipfile.ZipFile(BytesIO(default)).read("xl/workbook.xml")


def test_mutations_do_not_change_generator_version():
    files = binary_files()
    line = build_line()
    for mutation in line.mutations:
        mutated = apply_mutation(files, mutation["id"])
        assert line.generator_version == GENERATOR_VERSION
        assert mutated
    missing = apply_mutation(files, "missing-title-block")
    assert b"Cover / title block" not in missing["schematic.txt"]
    assert b"CV01 motor feeder" in missing["schematic.txt"]
    shuffled = apply_mutation(files, "shuffle-order")
    assert set(shuffled.values()) == set(files.values())
    renamed = apply_mutation(files, "rename-files")
    assert all(name.startswith("renamed-") for name in renamed)
    old = apply_mutation(files, "bom-rev-old")
    assert old["bom.xlsx"] == files["bom_rev_old.xlsx"]
    with pytest.raises(KeyError):
        apply_mutation(files, "not-a-mutation")


def test_unrelated_hvac_is_not_a_canonical_entity():
    oracle = build_oracle()
    ids = {item["id"] for item in oracle["entities"]}
    assert "DAT-9" not in ids
    assert "SF-9" not in ids
    assert any(doc["role"] == "unrelated" for doc in oracle["documents"])


def test_compare_helpers_score_exact_sets():
    gold = {"a", "b", "c"}
    assert score_ids({"a", "b", "c"}, gold)["f1"] == 1.0
    scored = score_ids({"a", "x"}, gold)
    assert scored["precision"] == 0.5
    assert scored["recall"] == pytest.approx(1 / 3, rel=1e-3)


def test_committed_oracle_matches_generator():
    expected = generated_files()
    extras = []
    for path in DATASET_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(DATASET_ROOT).as_posix()
        assert relative in expected, relative
        assert path.read_bytes() == expected[relative]
        extras.append(relative)
    assert set(extras) == set(expected)
    xml_in_conformance = list((DATASET_ROOT / "conformance").glob("*.xml"))
    assert xml_in_conformance == []


def test_evidence_locators_resolve_in_generated_sources():
    line = build_line()
    texts = source_texts(line)
    oracle = build_oracle(line)
    errors = evidence_resolution_errors(oracle, texts, extras=line.alarms)
    assert errors == []
    bom_rows = [row.split("\t") for row in texts["sources/bom.tsv"].splitlines()]
    motor = next(item for item in oracle["entities"] if item["id"] == "CV02-M1")
    assert motor["evidence"][0]["cell_range"] == f"A{first_column_row(bom_rows, 'CV02-M1')}"
    assert bom_rows[first_column_row(bom_rows, "CV02-M1") - 1][0] == "CV02-M1"
    alarm_table = [row.split("\t") for row in texts["sources/alarms.tsv"].splitlines()]
    jam = next(item for item in line.alarms if item["id"] == "ALM-CV02-JAM")
    assert jam["evidence"]["cell_range"] == f"A{first_column_row(alarm_table, 'ALM-CV02-JAM')}"
    ob1 = texts["sources/plc/OB1.scl"]
    fc_ref = next(item for item in oracle["plc_references"] if item["id"] == "ref-ob1-fc-mode")
    assert fc_ref["evidence"][0]["line_start"] == line_containing(ob1, "FC_Mode();")
    ctrl = texts["sources/plc/FB_ConveyorCtrl.scl"]
    start = next(item for item in oracle["plc_references"] if item["id"] == "ref-CV01-start")
    assert start["evidence"][0]["line_start"] == line_containing(ctrl, "RunCmd :=")
    assert start["evidence"][0]["line_end"] <= len(ctrl.splitlines())
    alarm_scl = texts["sources/plc/FB_Alarm.scl"]
    vfd = next(item for item in line.alarms if item["id"] == "ALM-CV01-VFD")
    assert vfd["evidence"]["line_start"] == line_containing(alarm_scl, "Beacon :=")
    assert vfd["evidence"]["line_end"] <= len(alarm_scl.splitlines())


def test_evidence_resolution_errors_catch_wrong_and_oob_locators():
    line = build_line()
    texts = source_texts(line)
    oracle = build_oracle(line)
    motor = next(item for item in oracle["entities"] if item["id"] == "CV02-M1")
    motor["evidence"][0]["cell_range"] = "A3"
    cell_errors = evidence_resolution_errors(oracle, texts)
    assert any("CV02-M1" in error and "holds" in error for error in cell_errors)
    oracle = build_oracle(line)
    start = next(item for item in oracle["plc_references"] if item["id"] == "ref-CV01-start")
    start["evidence"][0]["line_start"] = 99
    start["evidence"][0]["line_end"] = 99
    span_errors = evidence_resolution_errors(oracle, texts)
    assert any("ref-CV01-start" in error and "outside" in error for error in span_errors)


def test_check_covers_metadata_and_missing_root(tmp_path: Path):
    missing = tmp_path / "absent"
    assert _check(missing) == 1
    write_dataset(tmp_path)
    assert _check(tmp_path) == 0
    (tmp_path / "dialect.json").write_text("{}\n", encoding="utf-8")
    assert _check(tmp_path) == 1
    write_dataset(tmp_path)
    (tmp_path / "sources" / "cabinet_photo.png").unlink()
    assert _check(tmp_path) == 1
    write_dataset(tmp_path)
    (tmp_path / "LICENSE.md").write_text("tampered\n", encoding="utf-8")
    assert _check(tmp_path) == 1
    write_dataset(tmp_path)
    (tmp_path / "stray.txt").write_text("nope\n", encoding="utf-8")
    assert _check(tmp_path) == 1


def test_multipage_pdf_keeps_page_objects():
    pdf = build_multipage_pdf(["page one", "page two", "page three"])
    assert pdf.count(b"/Type /Page ") == 3
    assert pdf.startswith(b"%PDF-")
