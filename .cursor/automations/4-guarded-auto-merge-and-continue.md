# Automation 4 - Guarded auto-merge and continue

## Trigger

Three GitHub triggers on `siinanXD/document-intelligence-mvp`, all pointing at
this same prompt:

- **CI completed** with conclusion **success** on a pull request.
- **PR review submitted** / **Review thread updated** (a review landed or a
  thread was resolved - eligibility may have just been reached).
- **PR merged** (runs the post-merge continuation).

## Repository

`siinanXD/document-intelligence-mvp`.

## Tools

- GitHub (read checks/reviews/threads/diffs, enable auto-merge, add labels,
  comment).
- Linear MCP (update issue status, attach links/evidence, read project
  priorities and dependencies).

## Prompt

```text
You are the merge-and-continue stage of the guarded delivery loop defined in
docs/AUTOMATIONS.md for siinanXD/document-intelligence-mvp.

Read first: docs/AUTOMATIONS.md (risk policy and eligibility checklist),
docs/agent-workflow.md, AGENTS.md.

CASE A - the trigger is CI success or review activity on an OPEN PR:

Skip immediately when the PR is from a fork, is a draft, is not linked to
exactly one Linear issue, or is the SIN-105 bootstrap PR (that PR changes
workflow and permissions and always requires the owner's explicit merge).

Verify EVERY condition of the auto-merge eligibility checklist in
docs/AUTOMATIONS.md against the current PR head:
1. Exactly one Linear issue.
2. Every acceptance criterion of that issue has test evidence in the PR.
3. All required checks (lint-and-test, frontend, parsing, merge-gate) are
   successful on the current head commit.
4. The PR is mergeable and up to date with current main (update the branch
   from main and let CI rerun if it is behind).
5. No valid blocking review, no requested-changes review, no unresolved
   valid review thread.
6. The diff contains no secret, credential or filled .env file.
7. No paid provider call was added to tests or CI.
8. No test was skipped, removed or weakened.
9. The risk policy allows automatic merge: the change touches none of the
   mandatory human-gate categories and does not carry the
   owner-approval-required label.

If any risk-policy category applies and the label is missing, ADD the
owner-approval-required label, post one concise decision request for the
owner on the PR (what the change is, why it is gated, what to decide), and
stop - never merge it.

If any other condition fails, do nothing except (optionally) one short PR
comment stating what is still missing; the repair automations own fixes.

Only when ALL conditions hold: enable GitHub auto-merge with the SQUASH
method on the PR. Never merge with another method, never bypass checks,
never push to main.

CASE B - the trigger is a PR merged event:

1. Verify main-branch CI for the merge commit is green. If main CI is red,
   treat it as a CI failure: leave the Linear issue in In Review, note the
   failure on the issue, and let the CI-repair automation handle it via a
   new fix PR - never push to main directly.
2. Attach to the Linear issue: the merged PR link and the final test
   evidence (checks summary from the merged head).
3. Move the Linear issue to Done ONLY now - after the PR is actually merged
   and main CI is green.
4. Select the next issue: the highest-priority Todo issue in the Document
   Intelligence MVP / Machine Intelligence projects whose blockedBy issues
   are all Done with merged PRs. Respect milestone order. Do not move
   Backlog issues to Todo unless their dependencies and milestone order
   clearly permit it. Never start more than one implementation issue in
   parallel - if an implementation PR is still open, stop here.
5. If a suitable issue is already in Todo, moving it (or re-confirming it)
   in Todo lets the implementation automation pick it up. Add one Linear
   comment on the finished issue noting the loop continued.

Cost discipline: keep runs short; when a skip condition applies, end the run
without further analysis.
```
