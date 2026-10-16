# Development and testing

Install the editable package and development dependencies as shown in the README.
No external credentials, services, network access, or model downloads are used
by tests. Dependency installation requires package-index access once.

```bash
ruff check .
ruff format --check .
pytest --cov=portfolio_agent --cov-report=term-missing
```

The suite injects a fake clock instead of sleeping. Temporary SQLite databases
exercise real persistence, rollback, restart recovery, idempotency, and competing
scheduler instances. Pure simulated workers provide deterministic outputs and
failures. CLI tests cover a complete approval lifecycle. GitHub API mocking will
be added with the GitHub adapter; there is no adapter to mock in Phase 1.

Tests cover initial gating, priority and FIFO ties, global ownership, approval
waits, non-interruption, completion, bounded/capped retries, expired ownership,
checkpoint recovery, invalid decisions, unsupported schema rejection, safe
exceptions, configuration, audit, step limits, and between-stage budgets.

CI runs formatting, lint, and tests on Python 3.11, 3.12, and 3.13. Local execution
only verifies the interpreter available in the workspace; the CI matrix verifies
the other versions when GitHub runs the workflow. Dependency ranges are bounded
but not locked; a reproducible lockfile and pinned action SHAs can be introduced
with dependency maintenance policy before production deployment.

State schema version 1 has no migration tooling. Do not delete a state database
to fix a schema error without preserving a backup. For a disposable demo only,
choose a new `--state` path to start fresh. Structured stderr logs supplement the
transactional database audit. Inspect `status` and `audit` for normal debugging;
the CLI suppresses raw validation/exception text to avoid echoing sensitive input.
