"""Compare predicted machine-intelligence graphs against the SIN-99 oracle."""

from __future__ import annotations

from typing import Any


def id_set(items: list[dict[str, Any]], key: str = "id") -> set[str]:
    return {str(item[key]) for item in items}


def score_ids(predicted: set[str], gold: set[str]) -> dict[str, float]:
    true_positive = len(predicted & gold)
    precision = true_positive / len(predicted) if predicted else 1.0
    recall = true_positive / len(gold) if gold else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": true_positive,
        "predicted": len(predicted),
        "gold": len(gold),
    }


def profile_is_subset(oracle: dict[str, Any]) -> list[str]:
    """CI profile ids must be a subset of the full oracle, same generator."""
    errors: list[str] = []
    ci = oracle["profiles"]["ci"]
    if ci["generator_version"] != oracle["generator_version"]:
        errors.append("ci generator_version diverges from the full oracle")
    full_entities = id_set(oracle["entities"])
    for entity_id in ci.get("entity_ids") or []:
        if entity_id not in full_entities:
            errors.append(f"ci entity {entity_id} is not in the full oracle")
    full_io = {item["id"] for item in oracle["plc_variables"]}
    for name in ci.get("io_names") or []:
        if name not in full_io:
            errors.append(f"ci io {name} is not in the full oracle")
    full_claims = id_set(oracle["behavior_claims"])
    for claim_id in ci.get("behavior_claim_ids") or []:
        if claim_id not in full_claims:
            errors.append(f"ci behavior {claim_id} is not in the full oracle")
    full_scenarios = id_set(oracle["scenarios"])
    for scenario_id in ci.get("scenario_ids") or []:
        if scenario_id not in full_scenarios:
            errors.append(f"ci scenario {scenario_id} is not in the full oracle")
    return errors
