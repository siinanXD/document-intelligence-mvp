"""Stable prompt identity for every production generation path."""

import ast
import hashlib
from pathlib import Path

from app.providers.prompts import ASK_GROUNDED, DOCUMENT_PROFILE, GENERATION_EVAL_JUDGE

# Frozen hashes of the v1 system text. Changing the instructions requires
# bumping `version` and updating the matching hash here.
_ASK_GROUNDED_V1 = "85eec0af7946093d7151c7e2ddf59fa625dcdf13bdd5abcc003e0e29f2069700"
_DOCUMENT_PROFILE_V1 = "9d13d0c14c5c3b38d683a02ea2f9cad3d01117cc8e26b625707ccc28796652c9"
_GENERATION_EVAL_JUDGE_V1 = "55b5e2fbb5755bafebf8d09dce93acc52490de5d703245e7fae16c2ebf4943bf"
_GENERATION_METHODS = frozenset({"complete", "complete_structured"})
_KNOWN_PROMPTS = frozenset({"ASK_GROUNDED", "DOCUMENT_PROFILE", "GENERATION_EVAL_JUDGE"})


def test_current_prompts_have_stable_identities():
    assert ASK_GROUNDED.name == "ask_grounded"
    assert ASK_GROUNDED.version == "v1"
    assert DOCUMENT_PROFILE.name == "document_profile"
    assert DOCUMENT_PROFILE.version == "v1"
    assert GENERATION_EVAL_JUDGE.name == "generation_eval_judge"
    assert GENERATION_EVAL_JUDGE.version == "v1"


def test_changing_ask_grounded_v1_text_requires_a_version_bump():
    digest = hashlib.sha256(ASK_GROUNDED.system.encode()).hexdigest()
    assert digest == _ASK_GROUNDED_V1
    assert ASK_GROUNDED.version == "v1"


def test_changing_document_profile_v1_text_requires_a_version_bump():
    digest = hashlib.sha256(DOCUMENT_PROFILE.system.encode()).hexdigest()
    assert digest == _DOCUMENT_PROFILE_V1
    assert DOCUMENT_PROFILE.version == "v1"


def test_changing_generation_eval_judge_v1_text_requires_a_version_bump():
    digest = hashlib.sha256(GENERATION_EVAL_JUDGE.system.encode()).hexdigest()
    assert digest == _GENERATION_EVAL_JUDGE_V1
    assert GENERATION_EVAL_JUDGE.version == "v1"


def test_every_production_generation_call_passes_a_versioned_prompt():
    """Anonymous complete() strings cannot be identified later."""
    files = list(Path("app").rglob("*.py"))
    violations = []
    for path in files:
        if path.name in {"openai_provider.py", "base.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _GENERATION_METHODS:
                continue
            if not node.args:
                violations.append(f"{path}:{node.lineno} missing prompt argument")
                continue
            first = node.args[0]
            if not isinstance(first, ast.Name) or first.id not in _KNOWN_PROMPTS:
                violations.append(f"{path}:{node.lineno} first argument is not a versioned Prompt")
    assert violations == []
