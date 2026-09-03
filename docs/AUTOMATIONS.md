# Guarded autonomous delivery loop (SIN-105)

This document defines the automated delivery loop for
`siinanXD/document-intelligence-mvp`:

```
Linear issue (Todo)
  -> Cursor Cloud Agent implements on the issue branch
  -> bash .cursor/check.sh locally
  -> non-draft GitHub PR (repository template)
  -> GitHub CI (lint-and-test, frontend, parsing) + merge-gate
  -> independent review (Codex + Copilot)
  -> verified fixes on the same branch/PR
  -> guarded squash auto-merge
  -> Linear issue Done after merge + green main CI
  -> next unblocked Todo issue
```

The repository owner performs manual product testing only at milestone
boundaries. Normal low-risk implementation PRs merge without owner approval,
subject to every guard below. High-risk PRs always stop for one owner decision.

GitHub remains the code and implementation source of truth. Linear defines
issue scope, priority, milestones and dependencies. The existing rules in
`CLAUDE.md`, `AGENTS.md` and `docs/agent-workflow.md` stay authoritative; this
loop extends them and adds no second control plane.

## Components

| Piece | Where it lives |
| --- | --- |
| Automation definitions (triggers, tools, prompts) | `.cursor/automations/` |
| Guarded auto-merge policy and risk gates | this file + `docs/agent-workflow.md` |
| Mechanical merge gate for the risk label | `.github/workflows/merge-gate.yml` |
| CI checks | `.github/workflows/ci.yml` (`lint-and-test`, `frontend`, `parsing`) |
| Independent review | Codex (external) + Copilot review requested from CI |
| Local check equivalent | `bash .cursor/check.sh` |

The four Cursor Automations are created at
[cursor.com/automations](https://cursor.com/automations) from the definitions
in `.cursor/automations/`. The definitions are version-controlled here so that
prompt changes go through review like any other change.

## Review contract

The reviewers have separate responsibilities so one defect does not create
several competing repair rounds:

| Reviewer | Authoritative scope |
| --- | --- |
| Copilot | Project invariants in the five `.github/skills/*/SKILL.md` files; findings cite the applicable skill |
| Codex | Independent general correctness and regression review |
| Cursor Security Agent | Security-specific review |
| Cursor repair stage | Validate every finding on the current head, deduplicate it, and apply only confirmed fixes |

`.github/copilot-instructions.md` contains review style and cross-cutting
inspection guidance only. It must not duplicate the invariant rules stored in
the skills. Automation 3 reads both sources as one review contract.

## Risk policy: mandatory human gates

A pull request must NOT be auto-merged, and must instead receive the
`owner-approval-required` label and stop for one owner decision, when it
involves any of the following:

- authentication or authorization
- tenant-isolation rules or security boundaries
- secrets, credentials or external account permissions
- production deployment or infrastructure
- destructive database migrations (drops, irreversible data rewrites)
- deletion or data-retention semantics
- GitHub Actions workflows, rulesets, branch protection or agent permissions
- major dependency upgrades
- paid live-provider execution
- Safety PLC behavior
- actual machine control
- bypassing protected PLC blocks
- ambiguous or contradictory acceptance criteria
- changes a reviewer classified as high risk

For gated changes the loop still implements (when scope is clear), tests,
reviews and fully prepares the PR - then adds `owner-approval-required`,
requests one concise owner decision, and stops before merge. The
`merge-gate` workflow fails while `owner-approval-required` is present and
`owner-approved` is absent, so GitHub auto-merge cannot complete even if it
was enabled earlier. The owner releases the gate by adding `owner-approved`
alongside `owner-approval-required` - the required label stays in place for
the audit record, and `owner-approved` is what opens the gate. Removing
`owner-approval-required` is not the release mechanism.

## Auto-merge eligibility checklist

Automation 4 enables squash auto-merge only after verifying all of:

1. The PR corresponds to exactly one Linear issue.
2. Every acceptance criterion has test evidence in the PR.
3. All required GitHub checks (`lint-and-test`, `frontend`, `parsing`,
   `merge-gate`) are successful on the current head.
4. The PR is mergeable and based on the current `main`.
5. No valid blocking review remains.
6. No requested-changes review remains.
7. No unresolved valid review thread remains.
8. No secret, credential or filled `.env` file is present in the diff.
9. No paid provider call was added to tests or CI.
10. No test was skipped, removed or weakened.
11. The risk policy above allows automatic merge (no human-gate category, or
    `owner-approval-required` paired with `owner-approved`).

## Post-merge continuation and red-main recovery

The `pull_request` merged event fires before `.github/workflows/ci.yml` can
finish on `main`. Automation 4 therefore splits post-merge work:

| Event | Case | Allowed actions |
| --- | --- | --- |
| PR merged | B | Leave the Linear issue In Review. Comment that Done waits for `main` CI. Do not mark Done. Do not start the next issue. If `main` CI is already terminal in the same run, jump to C or D. |
| CI / workflow completed on `main`, success | C | Only now attach evidence, move Linear to Done, and select the next unblocked Todo issue. |
| CI / workflow completed on `main`, failure | D | Reachable recovery: keep the same Linear issue In Review, open one recovery PR from current `main` (same issue, not a replacement of an open PR) or escalate to the owner. Never push to `main`. |

Automation 2 cannot own red `main` CI: it is scoped to open pull-request
branches, skips merged PRs, and must not create a replacement PR. Case D is
the path that makes recovery reachable again (an open recovery PR that
Automation 2 can then repair, at most three rounds) or escalates to the
owner. After three unsuccessful recovery rounds, or when no safe automated
fix exists, Case D posts the blocker on GitHub and Linear, adds
`owner-approval-required` when a recovery PR exists, and stops. Linear does
not stay In Review with no actor: either a recovery PR is open, or the owner
has an explicit decision request.

High-risk recovery changes still take the `owner-approval-required` gate and
do not auto-merge.

## Safety and cost bounds

- Maximum one active implementation issue / implementation PR at a time.
- Maximum three automatic repair rounds per failing condition (CI failure or
  review finding). After the third unsuccessful round the loop stops, records
  the blocker on the PR and the Linear issue, and does not merge.
- No paid API calls from tests or CI - providers stay mocked.
- Secrets are never exposed to forks or untrusted PRs; repository-write
  automations skip fork PRs (`head.repo.full_name != repository`).
- No automatic deployment.
- No direct pushes to `main`, no force-pushes, no replacement PRs.
- The SIN-105 bootstrap PR itself changes workflow and permissions and is
  never auto-merged.

## Expected Cursor Cloud usage and billing

Each automation trigger starts one Cloud Agent run, billed against the Cursor
plan's usage like any other Cloud Agent request (model tokens plus the
Cloud Agent compute the run consumes). What bounds spend:

- One implementation issue at a time - Automation 1 exits immediately (a short,
  cheap run) when another implementation PR is active or the issue is blocked.
- Three repair rounds per failing condition cap CI/review repair loops.
- PR CI-completed and review triggers fire only on the single active PR;
  agents exit early when there is nothing valid to fix. The extra `main` CI
  trigger is one short run per merge (Case C or D).
- Runs reuse the prebuilt environment (`.cursor/environment.json` build), so
  install cost is paid at build time, not per run.
- Normal runs use no paid provider APIs; tests mock all providers.

A typical issue therefore costs one implementation run, zero to three short
repair runs, one merge-eligibility run, one PR-merged acknowledgement, and
one `main` CI continue/recovery run. Deactivate the automations at
[cursor.com/automations](https://cursor.com/automations) to pause the loop at
any time.

## Owner activation checklist (one-time, admin-only)

These settings cannot be changed by the agent (the agent's GitHub token is
read-only for repository administration; Cursor Automations and the Linear
integration are dashboard-scoped). Everything else is already in the
repository. Activate the loop by doing the following once:

**GitHub repository settings** (`Settings` -> `General`):

1. Enable **Allow auto-merge** (currently off).
2. Enable **Allow squash merging** and disable **Allow merge commits** and
   **Allow rebase merging** (squash-only keeps linear history).
3. Recommended: enable **Automatically delete head branches**.

**GitHub branch protection for `main`** (`Settings` -> `Branches` or rulesets).
Note: this repository is currently **private on the GitHub Free plan**, where
branch protection and rulesets are not available. Either upgrade the plan or
make the repository public first; auto-merge without required checks merges
immediately and must not be enabled without them. Then configure:

4. Require a pull request before merging; block direct pushes to `main`.
5. Block force pushes and branch deletion on `main`.
6. Require linear history.
7. Require status checks to pass before merging, with these required checks:
   `lint-and-test`, `frontend`, `parsing`, `merge-gate`.
8. Require conversation resolution before merging.
9. Do not grant coding agents (Cursor, Copilot, Codex) bypass permission on
   these rules; no actor except the owner should bypass protection.

**Labels** (`Issues` -> `Labels`):

10. Create the label `owner-approval-required` (suggested color `#B60205`,
    description: "High-risk change - one explicit owner decision required
    before merge").
11. Create the label `owner-approved` (description: "Owner decided: opens
    the gate. Only Sinan sets this."). The owner adds this label alongside
    `owner-approval-required` to release the `merge-gate` check;
    `owner-approval-required` stays on the PR as the audit record.

**Cursor dashboard** ([cursor.com/automations](https://cursor.com/automations)):

12. Create the four automations exactly as defined in `.cursor/automations/`
    (trigger, repository, tools, prompt per file) and activate them.
13. Ensure the Cursor Linear integration is connected to the workspace that
    contains the Document Intelligence MVP and Machine Intelligence projects,
    and that the Cursor GitHub connection has write access to
    `siinanXD/document-intelligence-mvp`.
14. If agents should update Linear directly (statuses, comments), authenticate
    the Linear MCP integration for Cloud Agents in the Cursor dashboard.

## Validation runbook

Run these once after activation, in order, to prove every state transition.
Each step uses a disposable low-risk change (docs/fixture/test only).

1. **Happy path** - move a small docs-only Linear issue to Todo. Expect:
   issue -> In Progress, branch + non-draft PR from the template, CI green,
   review requested, issue -> In Review.
2. **CI repair** - push (or include) a deliberately failing unit test on that
   PR branch. Expect: Automation 2 fixes it on the same PR with the smallest
   change, never a replacement PR, and never by deleting/weakening the test.
3. **Valid review finding** - leave a review comment describing a real defect.
   Expect: a regression test plus the smallest fix pushed to the same PR, and
   the thread resolved only after the fixed head is verified.
4. **Invalid review finding** - leave a plausible but wrong review comment.
   Expect: a reply explaining why it is incorrect, with no code change.
5. **High-risk gate** - move an issue touching a gated area (for example a
   GitHub Actions edit) to Todo. Expect: a complete PR carrying
   `owner-approval-required`, a failing `merge-gate` check, no auto-merge,
   and one concise decision request.
6. **Guarded auto-merge** - after the low-risk PR is green with reviews clean,
   expect squash auto-merge to complete without owner action.
7. **Linear Done timing** - after squash merge, the issue must stay In Review
   until the `main` CI-completed event (Automation 4 Case C). Only then attach
   evidence, move the issue to Done, and start the next unblocked Todo issue.
   The PR-merged event alone must not mark Done.
8. **Sequencing** - with two Todo issues where one blocks the other, verify
   only the unblocked one starts, and the second starts only after the first
   is Done and merged.
9. **Red main CI** - after a merge whose `main` CI fails, Automation 4 Case D
   must open one recovery PR for the same Linear issue or escalate to the
   owner. Automation 2 must not be the only recovery path. Linear must not
   stay In Review with no open recovery PR and no owner decision request.

Record the outcomes on SIN-105 before relying on the loop unattended.
