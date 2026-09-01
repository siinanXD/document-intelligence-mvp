"""CLI: python -m app.evaluation.machine_intelligence

Regenerate the committed machine-intelligence-v1 dataset from the generator.
`--check` compares the tree to a fresh generation and exits 1 on drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.machine_intelligence.artifacts import (
    DATASET_ROOT,
    committed_paths,
    source_texts,
    write_dataset,
)
from app.evaluation.machine_intelligence.line import build_line
from app.evaluation.machine_intelligence.oracle import build_oracle


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
    line = build_line()
    expected_oracle = json.dumps(build_oracle(line), indent=2, sort_keys=True) + "\n"
    actual_oracle = (root / "oracle.json").read_text(encoding="utf-8")
    mismatches = []
    if actual_oracle != expected_oracle:
        mismatches.append("oracle.json")
    for relative, body in source_texts(line).items():
        path = root / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            mismatches.append(relative)
    if mismatches:
        print("fixture drift:", ", ".join(mismatches[:20]), file=sys.stderr)
        return 1
    print(f"fixture matches generator at {root}")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    if args.check:
        if not committed_paths(args.root):
            raise SystemExit(1)
        raise SystemExit(_check(args.root))
    dest = write_dataset(args.root)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
