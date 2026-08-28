## Summary

<!-- What changed and why. Link the Linear issue, e.g. SIN-00. -->

Linear issue:

## Changes

-

## Test evidence

<!-- Paste the commands you ran and their result. -->

```
ruff check .
pytest
```

## Known limitations

<!-- What is deliberately out of scope, and anything a reviewer should watch for. -->

-

## Checklist

- [ ] Scope matches a single Linear issue
- [ ] `ruff check .` and `pytest` pass locally
- [ ] No secrets, credentials or filled `.env` in the diff
- [ ] Tenant isolation preserved for any new data path
- [ ] External providers mocked in tests
