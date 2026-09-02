"""SIN-105 delivery-loop contracts: main-CI continuation and red-main recovery."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_2 = ROOT / ".cursor" / "automations" / "2-repair-failed-ci.md"
AUTOMATION_4 = ROOT / ".cursor" / "automations" / "4-guarded-auto-merge-and-continue.md"
AUTOMATIONS_DOC = ROOT / "docs" / "AUTOMATIONS.md"
WORKFLOW = ROOT / "docs" / "agent-workflow.md"
MERGE_GATE = ROOT / ".github" / "workflows" / "merge-gate.yml"

# The two Codex P1 failure modes this file exists to keep closed.
PR_MERGED_WHILE_MAIN_CI_PENDING = {
    "trigger": "pr_merged",
    "pr_state": "merged",
    "main_ci": "pending",
}
MAIN_CI_RED_AFTER_MERGE = {
    "trigger": "main_ci",
    "pr_state": "merged",
    "main_ci": "failure",
}


def _prompt_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index("```text")
    end = text.index("```", start + 7)
    return text[start:end]


def _case_body(prompt: str, letter: str) -> str:
    marker = f"CASE {letter} -"
    start = prompt.index(marker)
    next_markers = [f"CASE {other} -" for other in "ABCD" if other != letter]
    ends = [prompt.find(m, start + len(marker)) for m in next_markers]
    ends = [e for e in ends if e != -1]
    end = min(ends) if ends else len(prompt)
    return prompt[start:end]


def next_loop_action(*, trigger: str, pr_state: str, main_ci: str | None) -> str:
    """Deterministic routing table from docs/AUTOMATIONS.md post-merge section.

    The automations are LLM prompts, not executable code. This table is the
    mechanical check that the documented cases still distinguish the events
    Codex showed can be collapsed.
    """
    if trigger in {"pr_ci_success", "review"}:
        if pr_state != "open":
            return "skip"
        return "case_a_evaluate_automerge"
    if trigger == "pr_merged":
        if main_ci == "success":
            return "case_c_mark_done_and_continue"
        if main_ci == "failure":
            return "case_d_recover_or_escalate"
        return "case_b_wait_for_main_ci"
    if trigger == "main_ci":
        if main_ci == "success":
            return "case_c_mark_done_and_continue"
        if main_ci == "failure":
            return "case_d_recover_or_escalate"
        return "skip"
    raise AssertionError(f"unknown trigger {trigger}")


def automation_2_handles(*, trigger: str, pr_state: str) -> bool:
    if trigger == "main_ci":
        return False
    if pr_state in {"merged", "closed", "none"}:
        return False
    return trigger == "pr_ci_failure" and pr_state == "open"


def test_pr_merged_while_main_ci_pending_waits_and_does_not_mark_done():
    action = next_loop_action(**PR_MERGED_WHILE_MAIN_CI_PENDING)
    assert action == "case_b_wait_for_main_ci"
    case_b = _case_body(_prompt_block(AUTOMATION_4), "B")
    lowered = case_b.lower()
    assert "do not move the linear issue to done" in lowered
    assert "do not select or start the next issue" in lowered
    assert "wait for the main-branch ci-completed trigger" in lowered
    assert "let the ci-repair automation handle it" not in lowered
    assert "move the linear issue to done only now" not in lowered


def test_main_ci_success_is_the_only_done_and_continue_path():
    assert (
        next_loop_action(trigger="main_ci", pr_state="merged", main_ci="success")
        == "case_c_mark_done_and_continue"
    )
    prompt = _prompt_block(AUTOMATION_4)
    case_c = _case_body(prompt, "C")
    assert "only path that may mark Linear Done" in case_c
    assert "Move the Linear issue to Done ONLY now" in case_c
    assert "Select the next issue" in case_c
    for letter in "ABD":
        body = _case_body(prompt, letter)
        assert "Move the Linear issue to Done ONLY now" not in body
        assert "Select the next issue:" not in body


def test_red_main_ci_has_reachable_recovery_that_automation_2_cannot_own():
    action = next_loop_action(**MAIN_CI_RED_AFTER_MERGE)
    assert action == "case_d_recover_or_escalate"
    assert automation_2_handles(trigger="main_ci", pr_state="merged") is False

    auto2 = AUTOMATION_2.read_text(encoding="utf-8")
    assert "never handles CI on `main`" in auto2
    assert "The failure is on branch `main`" in _prompt_block(AUTOMATION_2)
    assert "The PR is closed or already merged." in _prompt_block(AUTOMATION_2)
    assert "Never create a" in _prompt_block(AUTOMATION_2)
    assert "replacement PR." in _prompt_block(AUTOMATION_2)

    case_d = _case_body(_prompt_block(AUTOMATION_4), "D")
    collapsed = " ".join(case_d.lower().split())
    assert "automation 2 cannot repair this" in collapsed
    assert "open exactly one recovery pr" in collapsed
    assert "escalate to the owner" in collapsed
    assert "owner-approval-required" in collapsed
    assert "three unsuccessful recovery rounds" in collapsed
    assert "do not start the next issue" in collapsed
    assert "never push to main" in collapsed
    assert "does not stay in review with no actor" in collapsed


def test_automation_4_trigger_section_includes_main_ci_success_and_failure():
    header = AUTOMATION_4.read_text(encoding="utf-8").split("## Prompt", 1)[0]
    assert "branch **`main`**" in header or "branch **main**" in header
    assert "success or failure" in header.lower()
    assert "PR merged" in header
    assert "do not treat that event as CI completion" in header


def test_docs_keep_the_post_merge_routing_table():
    doc = AUTOMATIONS_DOC.read_text(encoding="utf-8")
    assert "Post-merge continuation and red-main recovery" in doc
    assert "PR merged" in doc
    assert "CI / workflow completed on `main`, success" in doc
    assert "CI / workflow completed on `main`, failure" in doc
    assert "Automation 2 cannot own red `main` CI" in doc
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Automation 4 Case C" in workflow
    assert "Automation 4 Case D" in workflow


def test_merge_gate_workflow_still_blocks_only_the_owner_label():
    # PyYAML 1.1 treats the key `on` as boolean True, so assert against source.
    text = MERGE_GATE.read_text(encoding="utf-8")
    assert "name: Merge gate" in text
    assert "labeled" in text and "unlabeled" in text
    assert "owner-approval-required" in text
    assert "exit 1" in text
    payload = yaml.safe_load(text)
    assert payload["name"] == "Merge gate"
    assert "merge-gate" in payload["jobs"]
