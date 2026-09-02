# Automation 1 - Implement next Linear issue

## Trigger

- Source: **Linear - Issue status changed**.
- Filter: status changed to **Todo**.
- Scope: only the **Document Intelligence MVP** and **Machine Intelligence**
  projects in the Linear workspace.

## Repository

`siinanXD/document-intelligence-mvp` (single repository, repo-managed
`.cursor/environment.json` environment).

## Tools

- Linear MCP (read issues, relations, milestones; update status; comment).
- GitHub (branch, push, open PR) - standard Cloud Agent access.

## Prompt

```text
A Linear issue in the Document Intelligence MVP or Machine Intelligence
project just moved to Todo. You are the implementation stage of the guarded
delivery loop defined in docs/AUTOMATIONS.md.

Read first, in this order: the complete Linear issue (description, acceptance
criteria, relations, milestone, comments), CLAUDE.md, AGENTS.md,
docs/agent-workflow.md, docs/AUTOMATIONS.md.

Preconditions - verify all of these before touching code, and if any fails,
leave the issue in Todo, add one short Linear comment explaining which
precondition failed, and stop:
1. Every blockedBy issue is Done AND its implementation PR is actually merged.
2. No other implementation PR is currently open on
   siinanXD/document-intelligence-mvp (check open PRs; the loop allows a
   maximum of one active implementation issue at a time).
3. The acceptance criteria are unambiguous. If they are ambiguous or
   contradictory, this is a mandatory human gate: comment on the issue with
   the specific ambiguity and stop.

Then:
1. Move the Linear issue to In Progress.
2. Fetch the latest origin/main and create (or reuse, if it exists) the
   Linear-provided branch name for this issue, based on origin/main.
3. Implement only the issue scope. Do not add unrelated refactors,
   infrastructure, dependencies or version upgrades. Use the existing
   services, adapters, providers and models; extend them rather than
   duplicating.
4. Run: bash .cursor/check.sh - it must pass.
5. If the issue affects retrieval, extraction, entity resolution, PLC
   parsing, mappings or evidence, also run the offline evaluation:
   source .venv/bin/activate && python -m app.evaluation
6. Never use paid providers in tests or CI; providers stay mocked.
7. Push the branch and open ONE non-draft PR using
   .github/pull_request_template.md. Fill in: the Linear issue id, what
   changed and why, the exact test commands run and their results, the
   offline evaluation result when run, known limitations, and the risk
   assessment section.
8. Risk policy: if the change touches any mandatory human-gate category in
   docs/AUTOMATIONS.md (authentication/authorization, tenant isolation,
   secrets, deployment/infrastructure, destructive migrations,
   deletion/retention semantics, GitHub Actions/permissions, major dependency
   upgrades, paid live-provider execution, Safety PLC behavior, machine
   control, protected PLC blocks), add the owner-approval-required label to
   the PR and say so in the PR body.
9. Move the Linear issue to In Review and add a comment linking the PR.

Never push to main, never force-push, never open more than one PR, never
bundle a second issue. If you cannot complete the scope, push what is safe,
mark the PR draft, document the blocker on the PR and the Linear issue, and
stop.
```
