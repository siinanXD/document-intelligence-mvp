"""Emit reviewable UTF-8 sources and binary evaluation files for SIN-99.

Text artifacts are committed. PDF/XLSX bytes are built at test time from those
sources, matching retrieval-v1. Synthetic SimaticML is labelled as generator
output and is not a TIA export.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

from app.evaluation.formats import build_file
from app.evaluation.machine_intelligence.dialect import (
    ENGINEERING_VERSION,
    SYNTHETIC_XML_BANNER,
    dialect_document,
)
from app.evaluation.machine_intelligence.line import (
    CPU_TYPE,
    DATASET_NAME,
    GENERATOR_VERSION,
    MACHINE_NAME,
    Line,
    build_line,
    conveyor_code,
    schematic_page_text,
)
from app.evaluation.machine_intelligence.oracle import build_oracle

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = REPO_ROOT / "evaluation" / "datasets" / DATASET_NAME
SOURCES = "sources"

BLOCK_ROOT_ELEMENT = {
    "OB": "SW.Blocks.OB",
    "FB": "SW.Blocks.FB",
    "FC": "SW.Blocks.FC",
    "DB": "SW.Blocks.GlobalDB",
}


def dataset_root(root: Path | None = None) -> Path:
    return root or DATASET_ROOT


def tsv(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell) for cell in rows) + "\n"


def source_texts(line: Line | None = None) -> dict[str, str]:
    line = line or build_line()
    files: dict[str, str] = {}
    files[f"{SOURCES}/overview.md"] = _overview(line)
    files[f"{SOURCES}/schematic.txt"] = _schematic(line)
    files[f"{SOURCES}/io_list.tsv"] = _io_list(line)
    files[f"{SOURCES}/hardware.tsv"] = _hardware_list(line)
    files[f"{SOURCES}/cables.tsv"] = _cable_list(line)
    files[f"{SOURCES}/terminals.tsv"] = _terminal_list(line)
    files[f"{SOURCES}/alarms.tsv"] = _alarm_list(line)
    files[f"{SOURCES}/bom.tsv"] = _bom(line, revision="A", quantity=1)
    files[f"{SOURCES}/bom_rev_old.tsv"] = _bom(line, revision="0", quantity=2)
    files[f"{SOURCES}/motor_drive.tsv"] = _motor_drive(line)
    files[f"{SOURCES}/commissioning.md"] = _commissioning(line)
    files[f"{SOURCES}/manufacturer_facts.json"] = (
        json.dumps(line.manufacturer_facts, indent=2, sort_keys=True) + "\n"
    )
    files[f"{SOURCES}/revision.md"] = _revision()
    files[f"{SOURCES}/unrelated_hvac.md"] = _unrelated()
    files[f"{SOURCES}/plc/cross_references.csv"] = _cross_references(line)
    files[f"{SOURCES}/s5/conveyor_start.awl"] = _s5_listing()
    files.update(_plc_sources(line))
    return files


def binary_files(line: Line | None = None) -> dict[str, bytes]:
    """PDF/XLSX/PNG built from the same generator as the committed sources."""
    line = line or build_line()
    texts = source_texts(line)
    files: dict[str, bytes] = {
        "overview.md": texts[f"{SOURCES}/overview.md"].encode(),
        "schematic.txt": texts[f"{SOURCES}/schematic.txt"].encode(),
        "schematic.pdf": build_multipage_pdf(
            [schematic_page_text(page, line) for page in line.schematic_pages]
        ),
        "io_list.xlsx": build_file("xlsx", texts[f"{SOURCES}/io_list.tsv"], sheet_name="IO")[0],
        "hardware.xlsx": build_file(
            "xlsx", texts[f"{SOURCES}/hardware.tsv"], sheet_name="Hardware"
        )[0],
        "cables.xlsx": build_file("xlsx", texts[f"{SOURCES}/cables.tsv"], sheet_name="Cables")[0],
        "terminals.xlsx": build_file(
            "xlsx", texts[f"{SOURCES}/terminals.tsv"], sheet_name="Terminals"
        )[0],
        "alarms.xlsx": build_file("xlsx", texts[f"{SOURCES}/alarms.tsv"], sheet_name="Alarms")[0],
        "bom.xlsx": build_file("xlsx", texts[f"{SOURCES}/bom.tsv"], sheet_name="BOM")[0],
        "bom_rev_old.xlsx": build_file(
            "xlsx", texts[f"{SOURCES}/bom_rev_old.tsv"], sheet_name="BOM"
        )[0],
        "motor_drive.xlsx": build_file(
            "xlsx", texts[f"{SOURCES}/motor_drive.tsv"], sheet_name="Drives"
        )[0],
        "commissioning.md": texts[f"{SOURCES}/commissioning.md"].encode(),
        "manufacturer_facts.json": texts[f"{SOURCES}/manufacturer_facts.json"].encode(),
        "revision.md": texts[f"{SOURCES}/revision.md"].encode(),
        "unrelated_hvac.md": texts[f"{SOURCES}/unrelated_hvac.md"].encode(),
        "cabinet_photo.png": minimal_png(),
        "plc/cross_references.csv": texts[f"{SOURCES}/plc/cross_references.csv"].encode(),
        "s5/conveyor_start.awl": texts[f"{SOURCES}/s5/conveyor_start.awl"].encode(),
    }
    for relative, body in texts.items():
        if relative.startswith(f"{SOURCES}/plc/") and relative.endswith((".xml", ".scl")):
            files[relative.removeprefix(f"{SOURCES}/")] = body.encode()
    return files


def write_dataset(root: Path | None = None) -> Path:
    dest = dataset_root(root)
    dest.mkdir(parents=True, exist_ok=True)
    line = build_line()
    oracle = build_oracle(line)
    (dest / "oracle.json").write_text(
        json.dumps(oracle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dest / "dialect.json").write_text(
        json.dumps(dialect_document(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dest / "manifest.json").write_text(
        json.dumps(_manifest(oracle), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dest / "LICENSE.md").write_text(_license(), encoding="utf-8")
    _write_conformance(dest / "conformance")
    for relative, body in source_texts(line).items():
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    photo = dest / SOURCES / "cabinet_photo.png"
    photo.write_bytes(minimal_png())
    return dest


def committed_paths(root: Path | None = None) -> dict[str, Path]:
    dest = dataset_root(root)
    mapping = {
        "oracle.json": dest / "oracle.json",
        "dialect.json": dest / "dialect.json",
        "manifest.json": dest / "manifest.json",
        "LICENSE.md": dest / "LICENSE.md",
        "conformance/README.md": dest / "conformance" / "README.md",
        "conformance/provenance.template.json": dest / "conformance" / "provenance.template.json",
        "sources/cabinet_photo.png": dest / SOURCES / "cabinet_photo.png",
    }
    for relative in source_texts():
        mapping[relative] = dest / relative
    return mapping


def _manifest(oracle: dict) -> dict:
    return {
        "name": DATASET_NAME,
        "version": GENERATOR_VERSION.removeprefix("machine-intelligence-"),
        "track": "machine_intelligence",
        "generator_version": GENERATOR_VERSION,
        "description": (
            "Synthetic 12-section conveyor line for Machine Intelligence. "
            "Not customer data. Not a TIA Portal project."
        ),
        "documents": oracle["documents"],
        "profiles": {
            "ci": "Conveyors CV01-CV03 plus shared line objects. Same oracle version.",
            "full": "All 12 conveyors, 30 schematic pages, full I/O.",
        },
        "conformance_sample": oracle["dialect"]["conformance_sample"],
    }


def _license() -> str:
    return (
        "# Licence\n\n"
        "All files in this dataset are original synthetic work for tests.\n"
        "They are not customer documents and they are not manufacturer manuals.\n"
        "Do not copy copyrighted datasheets into this tree. Cite synthetic facts\n"
        "with `https://example.invalid/...` URLs only.\n"
        "The SimaticML XML is generator output, not a TIA Portal export.\n"
    )


def _write_conformance(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text(
        "# SimaticML conformance sample (placeholder)\n\n"
        "SIN-99 requires a legally cleared, anonymized TIA Portal / Openness\n"
        "block export so SIN-93 does not co-evolve with the synthetic generator.\n\n"
        "That file is **not in this repository yet**. Do not treat any XML under\n"
        "`sources/plc/` as a TIA export. Those files are labelled synthetic.\n\n"
        "When the owner supplies a cleared export:\n\n"
        "1. Place it at `conformance/sample.xml`.\n"
        "2. Fill `provenance.template.json` and rename it to `provenance.json`.\n"
        "3. Keep Siemens namespaces and block/network structure; strip customer names.\n\n"
        "Until then, SIN-93 must not claim TIA-export compatibility.\n"
        "CI does not install TIA Portal.\n",
        encoding="utf-8",
    )
    (path / "provenance.template.json").write_text(
        json.dumps(
            {
                "status": "missing",
                "producer": "TIA Portal / Openness export",
                "tia_version": "",
                "export_command_or_ui_path": "",
                "export_date": "",
                "anonymized": True,
                "contains_customer_ip": False,
                "notes": (
                    "Fill when the owner supplies a legally cleared block export. "
                    "Do not replace this template with generator XML."
                ),
                "expected_path": "conformance/sample.xml",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _overview(line: Line) -> str:
    zones = ", ".join(f"{z['name']} ({z['id']})" for z in line.zones)
    return (
        f"# {MACHINE_NAME}\n\n"
        f"Synthetic multi-conveyor line `{line.machine_code}`.\n"
        f"CPU class: {line.machine_code} uses {CPU_TYPE} labels, central + distributed I/O.\n"
        f"Zones: {zones}.\n"
        f"Conveyors: {', '.join(c.code for c in line.conveyors)}.\n"
        f"Generator: {line.generator_version}.\n"
        "This overview is not a customer document.\n"
    )


def _schematic(line: Line) -> str:
    pages = [schematic_page_text(page, line) for page in line.schematic_pages]
    return "\n\f\n".join(pages) + "\n"


def _io_list(line: Line) -> str:
    rows = [["name", "address", "data_type", "direction", "comment", "conveyor"]]
    for point in line.io_points:
        rows.append(
            [
                point.name,
                point.address,
                point.data_type,
                point.direction,
                point.comment,
                point.conveyor_id or "",
            ]
        )
    return tsv(rows)


def _hardware_list(line: Line) -> str:
    rows = [["id", "name", "role", "location", "class"]]
    for item in line.hardware:
        attrs = item["attributes"]
        rows.append(
            [
                item["id"],
                item["name"],
                str(attrs.get("role", "")),
                str(attrs.get("location", "")),
                str(attrs.get("class", "")),
            ]
        )
    return tsv(rows)


def _cable_list(line: Line) -> str:
    rows = [["id", "from", "to"]]
    for item in line.cables:
        rows.append([item["id"], str(item["attributes"]["from"]), str(item["attributes"]["to"])])
    return tsv(rows)


def _terminal_list(line: Line) -> str:
    rows = [["id", "role", "cable"]]
    for item in line.terminals:
        rows.append([item["id"], str(item["attributes"]["role"]), str(item["attributes"]["cable"])])
    return tsv(rows)


def _alarm_list(line: Line) -> str:
    rows = [["id", "text", "source"]]
    for item in line.alarms:
        rows.append([item["id"], item["text"], item["source"]])
    return tsv(rows)


def _bom(line: Line, *, revision: str, quantity: int) -> str:
    rows = [["tag", "kind", "qty", "revision", "power_kw"]]
    for conveyor in line.conveyors:
        qty = str(quantity if conveyor.number == 1 else 1)
        rows.append([conveyor.motor_tag, "motor", qty, revision, str(conveyor.power_kw)])
        if conveyor.vfd_tag:
            rows.append([conveyor.vfd_tag, "vfd", "1", revision, ""])
    return tsv(rows)


def _motor_drive(line: Line) -> str:
    rows = [["motor", "drive", "kind", "power_kw"]]
    for conveyor in line.conveyors:
        rows.append(
            [
                conveyor.motor_tag,
                conveyor.vfd_tag or "DOL",
                conveyor.drive,
                str(conveyor.power_kw),
            ]
        )
    return tsv(rows)


def _commissioning(line: Line) -> str:
    checks = [
        "- [ ] E-stop circuit reports Line.EStopOk",
        "- [ ] Auto/manual mode mutually exclusive",
        "- [ ] Each conveyor starts in Auto after Reset",
        "- [ ] Jam on CV02 stops the motor and raises the beacon",
        "- [ ] Downstream inhibit: CV03 will not run unless CV04.Ready",
    ]
    for conveyor in line.conveyors:
        checks.append(f"- [ ] {conveyor.code} motor {conveyor.motor_tag} direction and feedback")
    return f"# Commissioning checklist — {line.machine_code}\n\n" + "\n".join(checks) + "\n"


def _revision() -> str:
    return (
        "# Revision record\n\n"
        "| Rev | Description |\n"
        "|-----|-------------|\n"
        "| 0 | First issue. CV01-M1 quantity 2 (superseded). |\n"
        "| A | Current. CV01-M1 quantity 1. |\n"
    )


def _unrelated() -> str:
    return (
        "# HVAC rooftop unit RTU-9\n\n"
        "This artifact is deliberately unrelated to Conveyor Line CL-12.\n"
        "Damper actuator DAT-9 and supply fan SF-9 must not enter the line graph.\n"
    )


def _cross_references(line: Line) -> str:
    rows = ["block,variable,kind,network"]
    rows.append("OB1,FC_Mode,call,1")
    for conveyor in line.conveyors:
        rows.append(f"FB_ConveyorCtrl,{conveyor.code}.Start,read,1")
        rows.append(f"FB_ConveyorCtrl,{conveyor.code}.RunCmd,write,1")
    return "\n".join(rows) + "\n"


def _s5_listing() -> str:
    return (
        "; Synthetic STEP 5 readable listing. Not a .S5D binary.\n"
        "; Optional fixture member for the S5 text path.\n"
        "A  I  0.0\n"
        "A  M  0.0\n"
        "=  Q  0.0\n"
    )


def _plc_sources(line: Line) -> dict[str, str]:
    files = {
        f"{SOURCES}/plc/OB1.xml": _simaticml("OB", "OB1", _ob1_networks(line)),
        f"{SOURCES}/plc/OB1.scl": _ob1_scl(line),
        f"{SOURCES}/plc/FB_ConveyorCtrl.xml": _simaticml(
            "FB", "FB_ConveyorCtrl", _fb_ctrl_networks()
        ),
        f"{SOURCES}/plc/FB_ConveyorCtrl.scl": _fb_ctrl_scl(),
        f"{SOURCES}/plc/FB_Alarm.xml": _simaticml("FB", "FB_Alarm", _fb_alarm_networks()),
        f"{SOURCES}/plc/FB_Alarm.scl": _fb_alarm_scl(),
        f"{SOURCES}/plc/FC_Mode.xml": _simaticml(
            "FC", "FC_Mode", ["AutoMode := Auto AND NOT Manual;"]
        ),
        f"{SOURCES}/plc/FC_Mode.scl": (
            "FUNCTION FC_Mode : Void\n"
            "VAR_INPUT\n"
            "  Auto : Bool;\n"
            "  Manual : Bool;\n"
            "END_VAR\n"
            "VAR_OUTPUT\n"
            "  AutoMode : Bool;\n"
            "END_VAR\n"
            "AutoMode := Auto AND NOT Manual;\n"
            "END_FUNCTION\n"
        ),
        f"{SOURCES}/plc/DB_Line.xml": _simaticml("DB", "DB_Line", ["// data"]),
    }
    return files


def _ob1_networks(_line: Line) -> list[str]:
    calls = ["FC_Mode();"]
    calls.extend(f"{conveyor_code(n)}Ctrl();" for n in range(1, 13))
    return calls


def _ob1_scl(line: Line) -> str:
    lines = [
        "ORGANIZATION_BLOCK OB1",
        "BEGIN",
        "  FC_Mode();",
    ]
    for number in range(1, 13):
        lines.append(f"  {conveyor_code(number)}Ctrl();")
    lines.extend(["END_ORGANIZATION_BLOCK", ""])
    return "\n".join(lines)


def _fb_ctrl_networks() -> list[str]:
    return [
        "RunCmd := Start AND NOT Stop AND AutoMode AND Ready AND NOT Jam;",
        "FaultLamp := Jam OR NOT ProtectFb;",
    ]


def _fb_ctrl_scl() -> str:
    return (
        "FUNCTION_BLOCK FB_ConveyorCtrl\n"
        "VAR_INPUT\n"
        "  Start : Bool;\n"
        "  Stop : Bool;\n"
        "  AutoMode : Bool;\n"
        "  Ready : Bool;\n"
        "  Jam : Bool;\n"
        "  ProtectFb : Bool;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "  RunCmd : Bool;\n"
        "  FaultLamp : Bool;\n"
        "END_VAR\n"
        "BEGIN\n"
        "  RunCmd := Start AND NOT Stop AND AutoMode AND Ready AND NOT Jam;\n"
        "  FaultLamp := Jam OR NOT ProtectFb;\n"
        "END_FUNCTION_BLOCK\n"
    )


def _fb_alarm_networks() -> list[str]:
    networks = [f"// network {n}" for n in range(1, 9)]
    networks.append("UNKNOWN_INSTRUCTION();")
    return networks


def _fb_alarm_scl() -> str:
    return (
        "FUNCTION_BLOCK FB_Alarm\n"
        "VAR_INPUT\n"
        "  Jam : Bool;\n"
        "  VfdFault : Bool;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "  Beacon : Bool;\n"
        "END_VAR\n"
        "BEGIN\n"
        "  Beacon := Jam OR VfdFault;\n"
        "  // Network 9 is unsupported in the XML fixture.\n"
        "END_FUNCTION_BLOCK\n"
    )


def _simaticml(block_type: str, name: str, networks: list[str]) -> str:
    root = BLOCK_ROOT_ELEMENT[block_type]
    units = []
    for index, statement in enumerate(networks, start=1):
        units.append(
            "      <SW.Blocks.CompileUnit ID="
            f'"{index}" CompositionName="CompileUnits">\n'
            "        <AttributeList>\n"
            "          <NetworkSource>\n"
            f"            <StatementList>{escape(statement)}</StatementList>\n"
            "          </NetworkSource>\n"
            "        </AttributeList>\n"
            "      </SW.Blocks.CompileUnit>"
        )
    language = "SCL" if block_type != "DB" else "DB"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!-- {SYNTHETIC_XML_BANNER} -->\n"
        "<Document>\n"
        f'  <Engineering version="{ENGINEERING_VERSION}"/>\n'
        "  <DocumentInfo>\n"
        "    <Created>2026-01-01T00:00:00</Created>\n"
        "    <ExportSetting>WithDefaults</ExportSetting>\n"
        "    <InstalledProducts>\n"
        "      <Product>\n"
        "        <DisplayName>synthetic-fixture</DisplayName>\n"
        f"        <DisplayVersion>{GENERATOR_VERSION}</DisplayVersion>\n"
        "      </Product>\n"
        "    </InstalledProducts>\n"
        "  </DocumentInfo>\n"
        f'  <{root} ID="0">\n'
        "    <AttributeList>\n"
        f"      <Name>{escape(name)}</Name>\n"
        f"      <ProgrammingLanguage>{language}</ProgrammingLanguage>\n"
        "    </AttributeList>\n"
        "    <ObjectList>\n" + "\n".join(units) + "\n    </ObjectList>\n"
        f"  </{root}>\n"
        "</Document>\n"
    )


def build_multipage_pdf(pages: list[str]) -> bytes:
    """Minimal multi-page PDF for schematic evidence. Independent of retrieval-v1."""
    from app.evaluation.formats import _pdf_escape

    page_streams = []
    for source in pages:
        lines = source.splitlines() or [""]
        operations = ["BT", "/F1 11 Tf", "50 780 Td"]
        y_steps = 0
        for index, line in enumerate(lines):
            if y_steps >= 52:
                operations.extend(["ET", "BT", "/F1 11 Tf", "50 780 Td"])
                y_steps = 0
            elif index:
                operations.append("0 -14 Td")
            operations.append(f"({_pdf_escape(line[:110])}) Tj")
            y_steps += 1
        operations.append("ET")
        page_streams.append("\n".join(operations).encode("latin-1"))

    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kid_refs = " ".join(f"{3 + index} 0 R" for index in range(len(page_streams)))
    objects.append(f"<< /Type /Pages /Kids [{kid_refs}] /Count {len(page_streams)} >>".encode())
    content_ids = []
    for index, stream in enumerate(page_streams):
        content_id = 3 + len(page_streams) + index
        content_ids.append((content_id, stream))
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 "
                f"{3 + 2 * len(page_streams)} 0 R >> >> >>"
            ).encode()
        )
    for _content_id, stream in content_ids:
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pieces = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(piece) for piece in pieces))
        pieces.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = sum(len(piece) for piece in pieces)
    xref = [b"xref\n", f"0 {len(objects) + 1}\n".encode(), b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode())
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return b"".join(pieces + xref + [trailer])


def minimal_png(width: int = 48, height: int = 32, shade: int = 180) -> bytes:
    """Tiny RGB PNG of a grey cabinet face. No PIL."""
    raw = b""
    row = bytes([shade, shade, min(255, shade + 20)] * width)
    for y in range(height):
        pixel = bytes([40, 40, 40] * width) if 8 <= y <= 12 else row
        raw += b"\x00" + pixel
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
