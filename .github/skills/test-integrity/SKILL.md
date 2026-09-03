---
name: test-integrity
description: A test is never deleted, skipped, loosened or rewritten to make CI green. Review every test change in a pull request against the reason the test existed.
---

# Test integrity

Most changes in this repository are written by automated agents, and an agent under pressure from a red pipeline has one very tempting move: make the test agree with the code. That move destroys the only evidence the system has. This skill exists to catch it.

The question for every changed test is not "does this look reasonable" but "what did this test prove before, and does it still prove it".

## What to check

A deleted test. Ask what it proved and whether another test now proves the same thing. If nothing does, the coverage is gone regardless of how the diff is described.

A newly added `@pytest.mark.skip`, `xfail`, `pytest.skip()` or a commented-out test body.

An assertion that got weaker: an exact value replaced by a range or a truthiness check, `assertEqual` turned into `assertIn`, a specific exception replaced by a bare `Exception`, an exact string replaced by a substring match, a count replaced by "greater than zero".

An expected value edited to match observed behaviour. If the code under test changed in the same pull request, this may be correct - then the pull request must say why the old expectation was wrong. If the code did not change, this is the code winning an argument it should have lost.

A narrowed input: a loop over cases reduced to one case, a fixture shrunk, a boundary case removed.

A test moved out of the CI selection - excluded by a marker, a path filter or a changed pytest invocation - while remaining in the repository. It looks alive and runs nowhere.

A `try`/`except` wrapped around an assertion, or a timeout raised until a flaky test passes.

## Not a finding

A test renamed, split or moved without changing what it asserts.

An assertion made stricter.

A test updated alongside a deliberate, documented behaviour change that the issue asked for - as long as the pull request states the old expectation and why it was wrong.

Removing a test that is genuinely duplicated by another named test.

Formatting, import order and fixture refactors that leave assertions intact.

## How to report

Quote the old assertion and the new one side by side. State what the old one proved. Then say which of these is true: the behaviour deliberately changed, the old test was wrong, or the test was weakened to pass. If it is the third, say so plainly - this is the finding the whole skill exists for.

