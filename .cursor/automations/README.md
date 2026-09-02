# Cursor Automation definitions

These files are the version-controlled source of truth for the four Cursor
Automations that run the guarded autonomous delivery loop described in
`docs/AUTOMATIONS.md`.

Cursor Automations live in the Cursor dashboard, not in the repository, so
each file here must be mirrored to one automation at
[cursor.com/automations](https://cursor.com/automations):

1. Create a new automation.
2. Configure the **Trigger** exactly as listed at the top of the file.
3. Set the **Repository** to `siinanXD/document-intelligence-mvp` (single
   repo, using the repo-managed `.cursor/environment.json` environment).
4. Enable the **Tools** listed in the file.
5. Paste the **Prompt** block verbatim.
6. Save and activate.

When a prompt needs to change, change it here first, merge it through the
normal PR flow, then update the dashboard copy. The prompts intentionally
reference `docs/AUTOMATIONS.md`, `docs/agent-workflow.md`, `AGENTS.md` and
`CLAUDE.md` so behavior updates in those files take effect without editing
every automation.

| File | Automation | Trigger source |
| --- | --- | --- |
| `1-implement-next-linear-issue.md` | Implement next Linear issue | Linear: status changed |
| `2-repair-failed-ci.md` | Repair failed CI | GitHub: CI completed / workflow run completed |
| `3-repair-review-findings.md` | Repair review findings | GitHub: review + PR pushed events |
| `4-guarded-auto-merge-and-continue.md` | Guarded auto-merge and continue | GitHub: PR CI success, review events, PR merged, **main** CI completed (success or failure) |
