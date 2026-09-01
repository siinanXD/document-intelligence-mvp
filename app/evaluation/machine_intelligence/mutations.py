"""Controlled fixture mutations. They do not change generator version."""

from __future__ import annotations

from collections.abc import Mapping


def apply_mutation(files: Mapping[str, bytes], mutation_id: str) -> dict[str, bytes]:
    """Return a new file map. Unknown ids raise KeyError."""
    mutated = dict(files)
    if mutation_id == "shuffle-order":
        return {key: mutated[key] for key in reversed(list(mutated))}
    if mutation_id == "rename-files":
        return {f"renamed-{key}": value for key, value in mutated.items()}
    if mutation_id == "missing-title-block":
        from app.evaluation.machine_intelligence.artifacts import build_multipage_pdf

        pages = mutated["schematic.txt"].decode("utf-8").split("\f")
        remaining = [page.strip("\n") for page in pages[1:] if page.strip()]
        mutated["schematic.txt"] = ("\n\f\n".join(remaining) + "\n").encode()
        mutated["schematic.pdf"] = build_multipage_pdf(remaining)
        return mutated
    if mutation_id == "degraded-photo":
        mutated["cabinet_photo.png"] = mutated["cabinet_photo.png"][:40] + b"\x00\x00"
        return mutated
    if mutation_id == "duplicate-overview":
        mutated["overview.copy.md"] = mutated["overview.md"]
        return mutated
    if mutation_id == "bom-rev-old":
        mutated["bom.xlsx"] = mutated["bom_rev_old.xlsx"]
        return mutated
    if mutation_id == "unrelated-hvac":
        return mutated
    if mutation_id == "reused-tag-comment":
        return mutated
    raise KeyError(mutation_id)
