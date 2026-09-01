"""Canonical multi-conveyor line used to emit the SIN-99 fixture and oracle.

The line is synthetic. Identifiers, addresses and evidence locators are
stable so later issues can compare against this generator version rather than
re-authoring truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.evaluation.machine_intelligence.tables import (
    alarm_rows,
    bom_rows,
    cable_rows,
    cell_a,
    fb_alarm_scl,
    first_column_row,
    line_containing,
    motor_drive_rows,
    ob1_scl,
    row_containing,
)

GENERATOR_VERSION = "machine-intelligence-v1.0.0"
DATASET_NAME = "machine-intelligence-v1"
MACHINE_CODE = "CL-12"
MACHINE_NAME = "Conveyor Line CL-12"
CPU_TYPE = "S7-1500-class"
ENGINEERING_VERSION = "V17"
PACKAGE_SLUG = "cl-12"
CI_CONVEYOR_NUMBERS = (1, 2, 3)
CI_PAGE_MAX = 14

ZONE_SPECS: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("zone-a", "Infeed", (1, 2, 3)),
    ("zone-b", "Transfer", (4, 5, 6)),
    ("zone-c", "Sortation", (7, 8, 9)),
    ("zone-d", "Outfeed", (10, 11, 12)),
)


@dataclass(frozen=True)
class Evidence:
    locator_kind: str
    artifact: str
    page_number: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    xml_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    native_object_id: str | None = None
    region: dict | None = None

    def as_dict(self) -> dict:
        payload = {"locator_kind": self.locator_kind, "artifact": self.artifact}
        if self.page_number is not None:
            payload["page_number"] = self.page_number
        if self.sheet_name is not None:
            payload["sheet_name"] = self.sheet_name
        if self.cell_range is not None:
            payload["cell_range"] = self.cell_range
        if self.xml_path is not None:
            payload["xml_path"] = self.xml_path
        if self.line_start is not None:
            payload["line_start"] = self.line_start
        if self.line_end is not None:
            payload["line_end"] = self.line_end
        if self.native_object_id is not None:
            payload["native_object_id"] = self.native_object_id
        if self.region:
            payload["region"] = self.region
        return payload


@dataclass
class IOPoint:
    name: str
    address: str
    data_type: str
    direction: str
    conveyor_id: str | None
    comment: str
    evidence: Evidence


@dataclass
class Conveyor:
    number: int
    code: str
    zone_id: str
    drive: str
    reversing: bool
    motor_tag: str
    vfd_tag: str | None
    power_kw: float
    sensors: list[str]
    io_names: list[str]


@dataclass
class Line:
    generator_version: str = GENERATOR_VERSION
    machine_code: str = MACHINE_CODE
    machine_name: str = MACHINE_NAME
    zones: list[dict] = field(default_factory=list)
    conveyors: list[Conveyor] = field(default_factory=list)
    io_points: list[IOPoint] = field(default_factory=list)
    schematic_pages: list[dict] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    cables: list[dict] = field(default_factory=list)
    terminals: list[dict] = field(default_factory=list)
    alarms: list[dict] = field(default_factory=list)
    hardware: list[dict] = field(default_factory=list)
    behavior_chains: list[dict] = field(default_factory=list)
    scenarios: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    mutations: list[dict] = field(default_factory=list)
    manufacturer_facts: list[dict] = field(default_factory=list)
    ci_conveyor_numbers: tuple[int, ...] = CI_CONVEYOR_NUMBERS


def conveyor_code(number: int) -> str:
    return f"CV{number:02d}"


def page_number(page_id: str) -> int:
    return int(page_id.split("-", 1)[1])


def _drive_for(number: int) -> tuple[str, bool, float]:
    if number % 3 == 2:
        return "dol", False, 2.2
    if number % 3 == 0:
        return "vfd", True, 4.0
    return "vfd", False, 5.5


def io_by_name(line: Line, name: str) -> IOPoint:
    return next(point for point in line.io_points if point.name == name)


def build_line() -> Line:
    line = Line()
    for zone_id, title, numbers in ZONE_SPECS:
        line.zones.append(
            {
                "id": zone_id,
                "code": zone_id,
                "name": title,
                "conveyor_numbers": list(numbers),
            }
        )

    next_di = 0
    next_do = 0
    next_ai = 64
    next_ao = 64

    def take_di() -> str:
        nonlocal next_di
        byte, bit = divmod(next_di, 8)
        next_di += 1
        return f"%I{byte}.{bit}"

    def take_do() -> str:
        nonlocal next_do
        byte, bit = divmod(next_do, 8)
        next_do += 1
        return f"%Q{byte}.{bit}"

    def take_ai() -> str:
        nonlocal next_ai
        address = f"%IW{next_ai}"
        next_ai += 2
        return address

    def take_ao() -> str:
        nonlocal next_ao
        address = f"%QW{next_ao}"
        next_ao += 2
        return address

    def add_io(
        *,
        name: str,
        address: str,
        data_type: str,
        direction: str,
        conveyor_id: str | None,
        row: int,
        comment: str,
        sheet: str = "IO",
    ) -> None:
        line.io_points.append(
            IOPoint(
                name=name,
                address=address,
                data_type=data_type,
                direction=direction,
                conveyor_id=conveyor_id,
                comment=comment,
                evidence=Evidence(
                    locator_kind="sheet_cell",
                    artifact="io_list.xlsx",
                    sheet_name=sheet,
                    cell_range=f"A{row}",
                ),
            )
        )

    shared_row = 2
    for name, address, data_type, direction, comment in (
        ("Line.EStopOk", take_di(), "Bool", "input", "Safety circuit healthy"),
        ("Line.Reset", take_di(), "Bool", "input", "Fault reset"),
        ("Line.AutoMode", take_di(), "Bool", "input", "Automatic mode"),
        ("Line.ManualMode", take_di(), "Bool", "input", "Manual mode"),
        ("Line.AlarmAck", take_di(), "Bool", "input", "Alarm acknowledge"),
        ("Line.Beacon", take_do(), "Bool", "output", "Stack light"),
        ("Line.Horn", take_do(), "Bool", "output", "Horn"),
    ):
        add_io(
            name=name,
            address=address,
            data_type=data_type,
            direction=direction,
            conveyor_id=None,
            row=shared_row,
            comment=comment,
        )
        shared_row += 1

    io_row = shared_row
    for number in range(1, 13):
        code = conveyor_code(number)
        zone_id = next(z for z, _title, nums in ZONE_SPECS if number in nums)
        drive, reversing, power_kw = _drive_for(number)
        motor_tag = f"{code}-M1"
        vfd_tag = f"{code}-U1" if drive == "vfd" else None
        sensors = [f"{code}-B1", f"{code}-B2", f"{code}-S1"]
        if number % 2 == 0:
            sensors.append(f"{code}-B3")
        io_names: list[str] = []
        digital = [
            (f"{code}.Start", take_di(), "input", "Local start"),
            (f"{code}.Stop", take_di(), "input", "Local stop"),
            (f"{code}.PEInfeed", take_di(), "input", "PE infeed"),
            (f"{code}.PEDischarge", take_di(), "input", "PE discharge"),
            (f"{code}.Jam", take_di(), "input", "Jam sensor"),
            (f"{code}.ContactorFb", take_di(), "input", "Contactor feedback"),
            (f"{code}.ProtectFb", take_di(), "input", "Protection feedback"),
            (f"{code}.Ready", take_di(), "input", "Section ready"),
            (f"{code}.RunCmd", take_do(), "output", "Run command"),
            (f"{code}.FaultLamp", take_do(), "output", "Fault lamp"),
        ]
        if reversing:
            digital.append((f"{code}.RevCmd", take_do(), "output", "Reverse command"))
        if drive == "vfd":
            digital.extend(
                (
                    (f"{code}.VfdFault", take_di(), "input", "VFD fault"),
                    (f"{code}.VfdReady", take_di(), "input", "VFD ready"),
                )
            )
        for name, address, direction, comment in digital:
            add_io(
                name=name,
                address=address,
                data_type="Bool",
                direction=direction,
                conveyor_id=code,
                row=io_row,
                comment=comment,
            )
            io_names.append(name)
            io_row += 1
        if drive == "vfd":
            for name, address, direction, comment in (
                (f"{code}.SpeedAct", take_ai(), "input", "Actual speed"),
                (f"{code}.SpeedSp", take_ao(), "output", "Speed setpoint"),
            ):
                add_io(
                    name=name,
                    address=address,
                    data_type="Int",
                    direction=direction,
                    conveyor_id=code,
                    row=io_row,
                    comment=comment,
                )
                io_names.append(name)
                io_row += 1
        line.conveyors.append(
            Conveyor(
                number=number,
                code=code,
                zone_id=zone_id,
                drive=drive,
                reversing=reversing,
                motor_tag=motor_tag,
                vfd_tag=vfd_tag,
                power_kw=power_kw,
                sensors=sensors,
                io_names=io_names,
            )
        )

    line.schematic_pages = _schematic_pages()
    line.blocks = _blocks()
    line.hardware = _hardware()
    line.components = _components(line)
    line.cables = _cables(line)
    line.terminals = _terminals(line)
    line.alarms = _alarms(line)
    line.connections = _connections(line)
    line.behavior_chains = _behavior_chains(line)
    line.scenarios = _scenarios()
    line.conflicts = [
        {
            "id": "conflict-reused-tag",
            "kind": "identity",
            "left_subject_kind": "entity",
            "left": "CV01.PEInfeed",
            "right_subject_kind": "entity",
            "right": "CV08.PEInfeed",
            "note": "Same comment text 'PE infeed' on two conveyors; must not merge.",
            "evidence": Evidence(
                locator_kind="sheet_cell",
                artifact="io_list.xlsx",
                sheet_name="IO",
                cell_range=io_by_name(line, "CV01.PEInfeed").evidence.cell_range,
            ).as_dict(),
        },
        {
            "id": "conflict-bom-revision",
            "kind": "revision",
            "left_subject_kind": "entity",
            "left": "CV01-M1",
            "right_subject_kind": "entity",
            "right": "CV01-M1",
            "note": "Current BOM quantity 1 disagrees with bom_rev_old.xlsx quantity 2.",
            "evidence": Evidence(
                locator_kind="sheet_cell",
                artifact="bom.xlsx",
                sheet_name="BOM",
                cell_range="A2",
            ).as_dict(),
        },
    ]
    line.unsupported = [
        {
            "id": "unsupported-unknown-instruction",
            "construct_code": "tia.unknown_instruction",
            "block": "FB_Alarm",
            "network_ordinal": 9,
            "evidence": Evidence(
                locator_kind="xml_path",
                artifact="plc/FB_Alarm.xml",
                xml_path="/Document/SW.Blocks.FB/ObjectList/SW.Blocks.CompileUnit[@ID='9']",
            ).as_dict(),
        }
    ]
    line.mutations = [
        {"id": "shuffle-order", "kind": "shuffle"},
        {"id": "rename-files", "kind": "rename"},
        {"id": "missing-title-block", "kind": "missing_page", "page": "E-01"},
        {"id": "degraded-photo", "kind": "degraded_photo"},
        {"id": "duplicate-overview", "kind": "duplicate"},
        {"id": "bom-rev-old", "kind": "revision"},
        {"id": "unrelated-hvac", "kind": "unrelated"},
        {"id": "reused-tag-comment", "kind": "reused_tag"},
    ]
    line.manufacturer_facts = [
        {
            "component_id": "CV01-M1",
            "manufacturer": "synthetic",
            "catalog_no": "CL12-MOT-5K5",
            "rated_power_kw": 5.5,
            "citation_url": "https://example.invalid/motors/CL12-MOT-5K5",
            "note": "Synthetic stand-in. Do not treat as a real manufacturer datasheet.",
        },
        {
            "component_id": "CV01-U1",
            "manufacturer": "synthetic",
            "catalog_no": "CL12-VFD-7K5",
            "rated_power_kw": 7.5,
            "citation_url": "https://example.invalid/drives/CL12-VFD-7K5",
            "compatible_with": ["CL12-MOT-5K5"],
        },
        {
            "component_id": "CV12-U1-wrong",
            "manufacturer": "synthetic",
            "catalog_no": "CL12-VFD-0K75",
            "rated_power_kw": 0.75,
            "citation_url": "https://example.invalid/drives/CL12-VFD-0K75",
            "incompatible_with": ["CL12-MOT-4K0"],
            "note": "Deliberately undersized candidate for negative mapping tests.",
        },
    ]
    return line


def _schematic_pages() -> list[dict]:
    pages = [
        {"id": "E-01", "title": "Cover / title block", "kind": "title"},
        {"id": "E-02", "title": "Incoming 400 VAC and main disconnect", "kind": "supply"},
        {"id": "E-03", "title": "Power distribution cabinet A", "kind": "distribution"},
        {"id": "E-04", "title": "Power distribution cabinet B", "kind": "distribution"},
        {"id": "E-05", "title": "24 VDC control supply", "kind": "control_supply"},
        {"id": "E-06", "title": "CPU and communications", "kind": "cpu"},
        {"id": "E-07", "title": "Distributed I/O ET200", "kind": "cpu"},
        {"id": "E-08", "title": "Digital inputs 1", "kind": "di"},
        {"id": "E-09", "title": "Digital inputs 2", "kind": "di"},
        {"id": "E-10", "title": "Digital outputs", "kind": "do"},
        {"id": "E-11", "title": "Analog I/O", "kind": "ao"},
    ]
    for number in range(1, 13):
        pages.append(
            {
                "id": f"E-{11 + number:02d}",
                "title": f"{conveyor_code(number)} motor feeder",
                "kind": "motor",
                "conveyor": conveyor_code(number),
            }
        )
    pages.extend(
        [
            {"id": "E-24", "title": "VFD power and fieldbus", "kind": "vfd"},
            {"id": "E-25", "title": "Terminal plan cabinet A", "kind": "terminals"},
            {"id": "E-26", "title": "Terminal plan cabinet B", "kind": "terminals"},
            {"id": "E-27", "title": "Cable list", "kind": "cables"},
            {"id": "E-28", "title": "Cross-page references", "kind": "xref"},
            {"id": "E-29", "title": "Revision / copied page (conflict)", "kind": "revision"},
            {"id": "E-30", "title": "Overview single-line", "kind": "overview"},
        ]
    )
    return pages


def _blocks() -> list[dict]:
    instances = [{"name": f"CV{n:02d}Ctrl", "of": "FB_ConveyorCtrl"} for n in range(1, 13)]
    blocks = [
        {
            "name": "OB1",
            "block_type": "OB",
            "language": "SCL",
            "role": "cyclic",
            "evidence": Evidence(
                locator_kind="xml_path",
                artifact="plc/OB1.xml",
                xml_path="/Document/SW.Blocks.OB/AttributeList/Name",
            ).as_dict(),
        },
        {
            "name": "FB_ConveyorCtrl",
            "block_type": "FB",
            "language": "SCL",
            "role": "reusable",
            "instances": instances,
            "evidence": Evidence(
                locator_kind="xml_path",
                artifact="plc/FB_ConveyorCtrl.xml",
                xml_path="/Document/SW.Blocks.FB/AttributeList/Name",
            ).as_dict(),
        },
        {
            "name": "FB_Alarm",
            "block_type": "FB",
            "language": "SCL",
            "role": "alarm",
            "evidence": Evidence(
                locator_kind="xml_path",
                artifact="plc/FB_Alarm.xml",
                xml_path="/Document/SW.Blocks.FB/AttributeList/Name",
            ).as_dict(),
        },
        {
            "name": "FC_Mode",
            "block_type": "FC",
            "language": "SCL",
            "role": "mode",
            "evidence": Evidence(
                locator_kind="xml_path",
                artifact="plc/FC_Mode.xml",
                xml_path="/Document/SW.Blocks.FC/AttributeList/Name",
            ).as_dict(),
        },
        {
            "name": "DB_Line",
            "block_type": "DB",
            "language": None,
            "role": "data",
            "evidence": Evidence(
                locator_kind="native_id",
                artifact="plc/DB_Line.xml",
                native_object_id="DB_Line",
            ).as_dict(),
        },
    ]
    for number in range(1, 13):
        instance_line = line_containing(ob1_scl(), f"CV{number:02d}Ctrl();")
        blocks.append(
            {
                "name": f"CV{number:02d}Ctrl",
                "block_type": "FB",
                "language": "SCL",
                "role": "instance",
                "instance_of": "FB_ConveyorCtrl",
                "evidence": Evidence(
                    locator_kind="line_range",
                    artifact="plc/OB1.scl",
                    line_start=instance_line,
                    line_end=instance_line,
                ).as_dict(),
            }
        )
    return blocks


def _hardware() -> list[dict]:
    return [
        {
            "id": "CL12-CPU",
            "kind": "component",
            "name": "CPU-CL12",
            "assembly_id": "zone-a",
            "attributes": {"role": "plc", "class": CPU_TYPE, "location": "Cabinet A"},
            "evidence": Evidence(
                locator_kind="sheet_cell",
                artifact="hardware.xlsx",
                sheet_name="Hardware",
                cell_range="A2",
            ).as_dict(),
        },
        {
            "id": "CL12-IO-A",
            "kind": "component",
            "name": "ET200-A",
            "assembly_id": "zone-a",
            "attributes": {"role": "distributed_io", "location": "Cabinet A"},
            "evidence": Evidence(
                locator_kind="sheet_cell",
                artifact="hardware.xlsx",
                sheet_name="Hardware",
                cell_range="A3",
            ).as_dict(),
        },
        {
            "id": "CL12-IO-B",
            "kind": "component",
            "name": "ET200-B",
            "assembly_id": "zone-d",
            "attributes": {"role": "distributed_io", "location": "Cabinet B"},
            "evidence": Evidence(
                locator_kind="sheet_cell",
                artifact="hardware.xlsx",
                sheet_name="Hardware",
                cell_range="A4",
            ).as_dict(),
        },
        {
            "id": "CL12-CAB-A",
            "kind": "component",
            "name": "Cabinet A",
            "assembly_id": "zone-a",
            "attributes": {"role": "cabinet"},
            "evidence": Evidence(
                locator_kind="page",
                artifact="schematic.pdf",
                page_number=3,
            ).as_dict(),
        },
        {
            "id": "CL12-CAB-B",
            "kind": "component",
            "name": "Cabinet B",
            "assembly_id": "zone-d",
            "attributes": {"role": "cabinet"},
            "evidence": Evidence(
                locator_kind="page",
                artifact="schematic.pdf",
                page_number=4,
            ).as_dict(),
        },
        {
            "id": "CL12-ESTOP",
            "kind": "component",
            "name": "S1-ESTOP",
            "assembly_id": "zone-a",
            "attributes": {"role": "safety_relay", "traceability_only": True},
            "evidence": Evidence(
                locator_kind="page",
                artifact="schematic.pdf",
                page_number=5,
            ).as_dict(),
        },
        {
            "id": "photo-cabinet-a",
            "kind": "other",
            "name": "Cabinet A photo",
            "assembly_id": "zone-a",
            "attributes": {"role": "photo"},
            "evidence": Evidence(
                locator_kind="image_region",
                artifact="cabinet_photo.png",
                region={"x": 0, "y": 0, "w": 48, "h": 32},
            ).as_dict(),
        },
    ]


def _components(line: Line) -> list[dict]:
    components = list(line.hardware)
    for conveyor in line.conveyors:
        motor_page = 11 + conveyor.number
        components.append(
            {
                "id": conveyor.motor_tag,
                "kind": "component",
                "name": conveyor.motor_tag,
                "assembly_id": conveyor.zone_id,
                "attributes": {"power_kw": conveyor.power_kw, "role": "motor"},
                "evidence": Evidence(
                    locator_kind="sheet_cell",
                    artifact="bom.xlsx",
                    sheet_name="BOM",
                    cell_range=cell_a(
                        first_column_row(
                            bom_rows(line, revision="A", quantity=1), conveyor.motor_tag
                        )
                    ),
                ).as_dict(),
            }
        )
        if conveyor.vfd_tag:
            components.append(
                {
                    "id": conveyor.vfd_tag,
                    "kind": "component",
                    "name": conveyor.vfd_tag,
                    "assembly_id": conveyor.zone_id,
                    "attributes": {"role": "vfd"},
                    "evidence": Evidence(
                        locator_kind="sheet_cell",
                        artifact="motor_drive.xlsx",
                        sheet_name="Drives",
                        cell_range=cell_a(row_containing(motor_drive_rows(line), conveyor.vfd_tag)),
                    ).as_dict(),
                }
            )
        for sensor in conveyor.sensors:
            components.append(
                {
                    "id": sensor,
                    "kind": "component",
                    "name": sensor,
                    "assembly_id": conveyor.zone_id,
                    "attributes": {"role": "sensor"},
                    "evidence": Evidence(
                        locator_kind="page",
                        artifact="schematic.pdf",
                        page_number=motor_page,
                    ).as_dict(),
                }
            )
    return components


def _cables(line: Line) -> list[dict]:
    cables = []
    for conveyor in line.conveyors:
        cables.append(
            {
                "id": f"W-{conveyor.code}-RUN",
                "kind": "cable",
                "name": f"W-{conveyor.code}-RUN",
                "assembly_id": conveyor.zone_id,
                "attributes": {"from": f"{conveyor.code}.RunCmd", "to": conveyor.motor_tag},
                "evidence": Evidence(
                    locator_kind="sheet_cell",
                    artifact="cables.xlsx",
                    sheet_name="Cables",
                    cell_range=cell_a(first_column_row(cable_rows(line), f"W-{conveyor.code}-RUN")),
                ).as_dict(),
            }
        )
    return cables


def _terminals(line: Line) -> list[dict]:
    terminals = []
    for conveyor in line.conveyors:
        cabinet = "A" if conveyor.number <= 6 else "B"
        page = 25 if cabinet == "A" else 26
        for suffix, role in (("1", "source"), ("2", "load")):
            terminals.append(
                {
                    "id": f"X{cabinet}:{conveyor.number}.{suffix}",
                    "kind": "terminal",
                    "name": f"X{cabinet}:{conveyor.number}.{suffix}",
                    "assembly_id": conveyor.zone_id,
                    "attributes": {"role": role, "cable": f"W-{conveyor.code}-RUN"},
                    "evidence": Evidence(
                        locator_kind="page",
                        artifact="schematic.pdf",
                        page_number=page,
                    ).as_dict(),
                }
            )
    return terminals


def _alarms(line: Line) -> list[dict]:
    beacon = line_containing(fb_alarm_scl(), "Beacon :=")
    alarms = []
    for row_number, row in enumerate(alarm_rows(line), start=1):
        if row_number == 1:
            continue
        alarm_id, text, source = row
        if alarm_id.endswith("-VFD"):
            evidence = Evidence(
                locator_kind="line_range",
                artifact="plc/FB_Alarm.scl",
                line_start=beacon,
                line_end=beacon,
            )
        else:
            evidence = Evidence(
                locator_kind="sheet_cell",
                artifact="alarms.xlsx",
                sheet_name="Alarms",
                cell_range=cell_a(row_number),
            )
        alarms.append(
            {
                "id": alarm_id,
                "code": alarm_id,
                "text": text,
                "source": source,
                "evidence": evidence.as_dict(),
            }
        )
    return alarms


def _connections(line: Line) -> list[dict]:
    connections = []
    for conveyor in line.conveyors:
        page = 11 + conveyor.number
        pe = io_by_name(line, f"{conveyor.code}.PEInfeed")
        run = io_by_name(line, f"{conveyor.code}.RunCmd")
        connections.append(
            {
                "id": f"{conveyor.code}-run-path",
                "kind": "connected_to",
                "source": run.name,
                "target": conveyor.motor_tag,
                "evidence": Evidence(
                    locator_kind="page",
                    artifact="schematic.pdf",
                    page_number=page,
                ).as_dict(),
            }
        )
        connections.append(
            {
                "id": f"{conveyor.code}-pe-infeed",
                "kind": "maps_to",
                "source": pe.name,
                "target": conveyor.sensors[0],
                "evidence": pe.evidence.as_dict(),
            }
        )
        connections.append(
            {
                "id": f"{conveyor.code}-cable",
                "kind": "connected_to",
                "source": f"X{'A' if conveyor.number <= 6 else 'B'}:{conveyor.number}.1",
                "target": f"X{'A' if conveyor.number <= 6 else 'B'}:{conveyor.number}.2",
                "evidence": Evidence(
                    locator_kind="sheet_cell",
                    artifact="cables.xlsx",
                    sheet_name="Cables",
                    cell_range=cell_a(first_column_row(cable_rows(line), f"W-{conveyor.code}-RUN")),
                ).as_dict(),
            }
        )
    connections.append(
        {
            "id": "ob1-calls-mode",
            "kind": "calls",
            "source": "OB1",
            "target": "FC_Mode",
            "evidence": Evidence(
                locator_kind="line_range",
                artifact="plc/OB1.scl",
                line_start=line_containing(ob1_scl(), "FC_Mode();"),
                line_end=line_containing(ob1_scl(), "FC_Mode();"),
            ).as_dict(),
        }
    )
    connections.append(
        {
            "id": "estop-maps-relay",
            "kind": "maps_to",
            "source": "Line.EStopOk",
            "target": "CL12-ESTOP",
            "evidence": Evidence(
                locator_kind="page",
                artifact="schematic.pdf",
                page_number=5,
            ).as_dict(),
        }
    )
    return connections


def _behavior_chains(line: Line) -> list[dict]:
    cv01 = next(c for c in line.conveyors if c.number == 1)
    cv02 = next(c for c in line.conveyors if c.number == 2)
    cv03 = next(c for c in line.conveyors if c.number == 3)
    cv04 = next(c for c in line.conveyors if c.number == 4)
    return [
        {
            "id": "chain-auto-start-zone-a",
            "kind": "sequence",
            "path": [
                "Line.AutoMode",
                "FC_Mode",
                "CV03.RunCmd",
                "CV02.RunCmd",
                "CV01.RunCmd",
                cv01.motor_tag,
                "CV01.PEDischarge",
            ],
            "evidence": [
                io_by_name(line, "Line.AutoMode").evidence.as_dict(),
                Evidence(
                    locator_kind="xml_path",
                    artifact="plc/FC_Mode.xml",
                    xml_path="/Document/SW.Blocks.FC/ObjectList/SW.Blocks.CompileUnit[@ID='1']",
                ).as_dict(),
                Evidence(
                    locator_kind="page",
                    artifact="schematic.pdf",
                    page_number=12,
                ).as_dict(),
            ],
        },
        {
            "id": "chain-downstream-inhibit",
            "kind": "interlock",
            "path": [
                f"{cv04.code}.Ready",
                "FB_ConveyorCtrl",
                f"{cv03.code}.RunCmd",
                cv03.motor_tag,
            ],
            "evidence": [
                io_by_name(line, f"{cv04.code}.Ready").evidence.as_dict(),
                Evidence(
                    locator_kind="xml_path",
                    artifact="plc/FB_ConveyorCtrl.xml",
                    xml_path="/Document/SW.Blocks.FB/ObjectList/SW.Blocks.CompileUnit[@ID='1']",
                ).as_dict(),
                Evidence(
                    locator_kind="page",
                    artifact="schematic.pdf",
                    page_number=14,
                ).as_dict(),
            ],
        },
        {
            "id": "chain-jam-stop",
            "kind": "alarm_chain",
            "path": [
                f"{cv02.code}.Jam",
                "FB_Alarm",
                f"{cv02.code}.RunCmd",
                cv02.motor_tag,
                "Line.Beacon",
            ],
            "evidence": [
                io_by_name(line, f"{cv02.code}.Jam").evidence.as_dict(),
                Evidence(
                    locator_kind="xml_path",
                    artifact="plc/FB_Alarm.xml",
                    xml_path="/Document/SW.Blocks.FB/ObjectList/SW.Blocks.CompileUnit[@ID='2']",
                ).as_dict(),
                io_by_name(line, "Line.Beacon").evidence.as_dict(),
            ],
        },
    ]


def _scenarios() -> list[dict]:
    return [
        {"id": "startup-auto", "chain_id": "chain-auto-start-zone-a"},
        {"id": "downstream-blocked", "chain_id": "chain-downstream-inhibit"},
        {"id": "jam-missing-speed", "chain_id": "chain-jam-stop"},
        {"id": "vfd-fault-cv01", "notes": "VfdFault stops CV01 and raises FB_Alarm."},
        {"id": "protect-fb-mismatch", "notes": "ProtectFb disagreeing with RunCmd is a conflict."},
        {
            "id": "sensor-implausible",
            "notes": "PEInfeed and PEDischarge both true while Jam is true.",
        },
        {"id": "reset-restart", "notes": "Line.Reset after EStopOk restores AutoMode sequence."},
    ]


def ci_subset(line: Line) -> dict:
    """Id sets for the fast CI profile. Same generator version as the full line."""
    numbers = set(line.ci_conveyor_numbers)
    conveyor_ids = {conveyor_code(n) for n in numbers}
    page_ids = [
        page["id"] for page in line.schematic_pages if page_number(page["id"]) <= CI_PAGE_MAX
    ]
    component_ids = [
        item["id"]
        for item in line.components
        if item["id"] in {"CL12-CPU", "CL12-IO-A", "CL12-CAB-A", "CL12-ESTOP", "photo-cabinet-a"}
        or any(item["id"].startswith(code) for code in conveyor_ids)
    ]
    return {
        "generator_version": line.generator_version,
        "conveyor_ids": sorted(conveyor_ids),
        "io_names": [
            point.name
            for point in line.io_points
            if point.conveyor_id in conveyor_ids or point.conveyor_id is None
        ],
        "page_ids": page_ids,
        "component_ids": component_ids,
        "behavior_claim_ids": ["chain-auto-start-zone-a"],
        "scenario_ids": [item["id"] for item in line.scenarios],
    }


def schematic_page_text(page: dict, line: Line) -> str:
    number = page_number(page["id"])
    xref = "E-30" if page["id"] != "E-30" else "E-01"
    lines = [
        f"TITLE BLOCK  machine={line.machine_code}  page={page['id']}  sheet={number}/30",
        f"Drawing: {page['title']}",
        f"CPU class: {CPU_TYPE}   cabinets: A, B   see also {xref}",
        f"Engineering: {ENGINEERING_VERSION}  generator={line.generator_version}",
        "",
    ]
    conveyor = page.get("conveyor")
    if conveyor:
        item = next(c for c in line.conveyors if c.code == conveyor)
        run = io_by_name(line, f"{item.code}.RunCmd")
        lines.extend(
            [
                f"Motor feeder {item.motor_tag}  drive={item.drive}  {item.power_kw} kW",
                f"Run command {run.name} at {run.address} -> terminal path W-{item.code}-RUN",
                f"Sensors: {', '.join(item.sensors)}",
                "Cross-ref: power on E-03, VFD on E-24, terminals on E-25/E-26",
            ]
        )
    elif page["kind"] == "xref":
        lines.append("Cross-page references:")
        for item in line.conveyors:
            motor_page = f"E-{11 + item.number:02d}"
            lines.append(f"  {item.code} motor {motor_page} <-> VFD E-24 <-> terminals E-25/E-26")
    elif page["kind"] == "title":
        lines.append(f"{line.machine_name} — synthetic multi-conveyor fixture")
        lines.append("Not a customer drawing. Title block present for mutation tests.")
    else:
        lines.append("Shared electrical page. Tags appear on motor feeder sheets E-12..E-23.")
        lines.append("Line.EStopOk  Line.AutoMode  Line.Beacon  see E-08 / E-10")
    return "\n".join(lines)
