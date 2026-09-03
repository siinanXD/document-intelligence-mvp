"""Deterministic extraction of component, signal and terminal identifiers."""

from __future__ import annotations

from app.engineering_models import EngineeringDocumentClass, EntityKind
from app.evaluation.machine_intelligence.artifacts import binary_files, source_texts
from app.services.entity_extract import MAX_IDENTIFIER_LENGTH, extract_mentions


def test_io_list_extracts_signals_with_addresses():
    files = binary_files()
    mentions = extract_mentions(
        content=files["io_list.xlsx"],
        filename="io_list.xlsx",
        path_hint="io_list.xlsx",
        document_class=EngineeringDocumentClass.io_list,
    )
    by_name = {item.name: item for item in mentions}
    assert by_name["CV01.PEInfeed"].entity_kind == EntityKind.signal
    assert by_name["CV01.PEInfeed"].attributes["address"] == "%I0.7"
    assert by_name["CV01.PEInfeed"].attributes["comment"] == "PE infeed"
    assert (
        by_name["CV08.PEInfeed"].attributes["address"]
        != by_name["CV01.PEInfeed"].attributes["address"]
    )
    assert by_name["CV01.PEInfeed"].evidence["sheet_name"] == "IO"
    assert by_name["CV01.PEInfeed"].evidence["cell_range"] == "A11"


def test_bom_and_old_revision_are_the_same_identifier():
    files = binary_files()
    current = extract_mentions(
        content=files["bom.xlsx"],
        filename="bom.xlsx",
        path_hint="bom.xlsx",
        document_class=EngineeringDocumentClass.bom,
    )
    old = extract_mentions(
        content=files["bom_rev_old.xlsx"],
        filename="bom_rev_old.xlsx",
        path_hint="bom_rev_old.xlsx",
        document_class=EngineeringDocumentClass.bom,
    )
    motor = next(item for item in current if item.name == "CV01-M1")
    old_motor = next(item for item in old if item.name == "CV01-M1")
    assert motor.revision_role == "current"
    assert old_motor.revision_role == "superseded"
    assert motor.attributes["qty"] == 1
    assert old_motor.attributes["qty"] == 2
    assert motor.evidence["sheet_name"] == "BOM"


def test_schematic_pages_yield_cross_page_identifiers():
    files = binary_files()
    mentions = extract_mentions(
        content=files["schematic.pdf"],
        filename="schematic.pdf",
        path_hint="schematic.pdf",
        document_class=EngineeringDocumentClass.schematic,
    )
    names = {item.name for item in mentions}
    assert "CV01-M1" in names
    assert "CV01-B1" in names
    assert "CV01.RunCmd" in names
    assert "W-CV01-RUN" in names
    motor = next(item for item in mentions if item.name == "CV01-M1")
    assert motor.evidence["locator_kind"] == "page"
    assert motor.evidence["page_number"] == 12


def test_unrelated_artifact_yields_no_mentions():
    texts = source_texts()
    mentions = extract_mentions(
        content=texts["sources/unrelated_hvac.md"].encode(),
        filename="unrelated_hvac.md",
        path_hint="unrelated_hvac.md",
        document_class=EngineeringDocumentClass.unrelated,
    )
    assert mentions == ()


def test_hardware_name_is_an_alias():
    files = binary_files()
    mentions = extract_mentions(
        content=files["hardware.xlsx"],
        filename="hardware.xlsx",
        path_hint="hardware.xlsx",
        document_class=EngineeringDocumentClass.hardware,
    )
    cpu = next(item for item in mentions if item.name == "CL12-CPU")
    assert "CPU-CL12" in cpu.aliases


def test_text_line_locators_use_precomputed_offsets():
    body = "header\nCV01.Alpha\nCV01.Beta\n"
    mentions = extract_mentions(
        content=body.encode(),
        filename="notes.txt",
        path_hint="notes.txt",
        document_class=EngineeringDocumentClass.manual,
    )
    by_name = {item.name: item for item in mentions}
    assert by_name["CV01.Alpha"].evidence["line_start"] == 2
    assert by_name["CV01.Beta"].evidence["line_start"] == 3


def test_overlong_table_identifier_is_skipped():
    long_name = "T" * (MAX_IDENTIFIER_LENGTH + 1)
    content = (
        f"tag,kind,qty,revision,power_kw\n{long_name},motor,1,A,1\nCV01-M1,motor,1,A,5.5\n"
    ).encode()
    mentions = extract_mentions(
        content=content,
        filename="bom.csv",
        path_hint="bom.csv",
        document_class=EngineeringDocumentClass.bom,
    )
    names = {item.name for item in mentions}
    assert "CV01-M1" in names
    assert long_name not in names


def test_extraction_is_reproducible():
    files = binary_files()
    first = extract_mentions(
        content=files["terminals.xlsx"],
        filename="terminals.xlsx",
        path_hint="terminals.xlsx",
        document_class=EngineeringDocumentClass.terminals,
    )
    second = extract_mentions(
        content=files["terminals.xlsx"],
        filename="terminals.xlsx",
        path_hint="terminals.xlsx",
        document_class=EngineeringDocumentClass.terminals,
    )
    assert first == second
    assert any(item.name == "XA:1.1" and item.entity_kind.value == "terminal" for item in first)
