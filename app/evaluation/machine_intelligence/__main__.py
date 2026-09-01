"""CLI: python -m app.evaluation.machine_intelligence

Regenerate the committed machine-intelligence-v1 dataset from the generator.
`--check` compares the tree to a fresh generation and exits 1 on drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.evaluation.machine_intelligence.artifacts import (
    DATASET_ROOT,
    generated_files,
    write_dataset,
)
from app.evaluation.machine_intelligence.line import build_line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Regenerate the SIN-99 fixture.")
    parser.add_argument("--root", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write. Fail if committed files differ from the generator.",
    )
    return parser


def _check(root: Path) -> int:
    if not root.is_dir():
        print(f"fixture root missing: {root}", file=sys.stderr)
        return 1
    expected = generated_files(build_line())
    mismatches: list[str] = []
    for relative, body in expected.items():
        path = root / relative
        if not path.is_file():
            mismatches.append(f"missing:{relative}")
        elif path.read_bytes() != body:
            mismatches.append(f"changed:{relative}")
    extras = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in expected:
            extras.append(relative)
    if extras:
        mismatches.extend(f"extra:{name}" for name in sorted(extras))
    if mismatches:
        print("fixture drift:", ", ".join(mismatches[:20]), file=sys.stderr)
        return 1
    print(f"fixture matches generator at {root}")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    if args.check:
        raise SystemExit(_check(args.root))
    dest = write_dataset(args.root)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
