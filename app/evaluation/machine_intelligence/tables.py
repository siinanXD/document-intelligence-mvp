"""Table and SCL text shared by the generator, oracle locators and emitters.

Row and line numbers used as evidence must come from these builders so a
locator cannot point at a different row than the committed source.
"""

from __future__ import annotations

from typing import Any


def tsv(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell) for cell in rows) + "\n"


def first_column_row(rows: list[list[str]], key: str) -> int:
    for index, row in enumerate(rows, start=1):
        if row and row[0] == key:
            return index
    raise KeyError(key)


def row_containing(rows: list[list[str]], key: str) -> int:
    for index, row in enumerate(rows, start=1):
        if key in row:
            return index
    raise KeyError(key)


def cell_a(row: int) -> str:
    return f"A{row}"


def line_containing(source: str, needle: str) -> int:
    for index, text in enumerate(source.splitlines(), start=1):
        if needle in text:
            return index
    raise KeyError(needle)


def bom_rows(line: Any, *, revision: str, quantity: int) -> list[list[str]]:
    rows = [["tag", "kind", "qty", "revision", "power_kw"]]
    for conveyor in line.conveyors:
        qty = str(quantity if conveyor.number == 1 else 1)
        rows.append([conveyor.motor_tag, "motor", qty, revision, str(conveyor.power_kw)])
        if conveyor.vfd_tag:
            rows.append([conveyor.vfd_tag, "vfd", "1", revision, ""])
    return rows


def cable_rows(line: Any) -> list[list[str]]:
    rows = [["id", "from", "to"]]
    for conveyor in line.conveyors:
        rows.append([f"W-{conveyor.code}-RUN", f"{conveyor.code}.RunCmd", conveyor.motor_tag])
    return rows


def motor_drive_rows(line: Any) -> list[list[str]]:
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
    return rows


def alarm_rows(line: Any) -> list[list[str]]:
    rows = [["id", "text", "source"]]
    rows.append(["ALM-ESTOP", "Emergency stop not healthy", "Line.EStopOk"])
    for conveyor in line.conveyors:
        rows.append([f"ALM-{conveyor.code}-JAM", f"{conveyor.code} jam", f"{conveyor.code}.Jam"])
        if conveyor.drive == "vfd":
            rows.append(
                [
                    f"ALM-{conveyor.code}-VFD",
                    f"{conveyor.code} VFD fault",
                    f"{conveyor.code}.VfdFault",
                ]
            )
    return rows


def ob1_scl() -> str:
    from app.evaluation.machine_intelligence.line import conveyor_code

    lines = [
        "ORGANIZATION_BLOCK OB1",
        "BEGIN",
        "  FC_Mode();",
    ]
    for number in range(1, 13):
        lines.append(f"  {conveyor_code(number)}Ctrl();")
    lines.extend(["END_ORGANIZATION_BLOCK", ""])
    return "\n".join(lines)


def fb_ctrl_scl() -> str:
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


def fb_alarm_scl() -> str:
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
