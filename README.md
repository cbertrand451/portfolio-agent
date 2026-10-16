# GitHub Portfolio Agent

A fully open-source orchestration foundation for an agent that will turn approved
GitHub projects into reviewed portfolio case studies. **Phase 1 only:** everything
runs locally with deterministic simulated jobs. No GitHub credentials, paid APIs,
model downloads, or inference hardware are required.

The main portfolio website (`cbertrand451.github.io`) is outside this project's
scope. This milestone does not generate pages, contact GitHub, or publish content.

## Setup

Python 3.11+ on Linux, macOS, or Windows:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
pytest
```

## Simulate a workflow

Run commands from the repository root. Each invocation reopens the same database.
Copy the `id` printed by `enqueue` into the subsequent commands:

```bash
portfolio-agent enqueue cbertrand451/example --key initial-v1
portfolio-agent status
portfolio-agent ready TASK_ID
portfolio-agent run
portfolio-agent status
portfolio-agent approve TASK_ID
portfolio-agent run
portfolio-agent audit
```

The flow is `QUEUED → READY → analyze → approval wait → READY → render → COMPLETED`.
An approval wait consumes no worker. Enqueue another repository and mark it ready
to see it progress while the first waits. `ready` approves initial execution;
`approve` approves a pending stage. Neither command executes work directly.
`reject TASK_ID` cancels a non-running, non-terminal task. Invalid or repeated
decisions fail with exit code 2 without changing state.

```bash
portfolio-agent enqueue cbertrand451/another --key demo-v1 --priority 5 --fail-once
portfolio-agent ready TASK_ID
portfolio-agent run --max-steps 1
portfolio-agent status
# After available_at (Unix timestamp), invoke run again to process the retry.
portfolio-agent run
```

`run` drains eligible work until idle, a step limit (default 100), or its configured
time budget. It never sleeps waiting for a retry or approval. Run it again after
an approval or when a retry becomes due. The CLI is a local trusted-user tool,
not a GitHub command handler or a background service. `python -m portfolio_agent`
is an equivalent entry point.

Configuration is optional:

```bash
portfolio-agent --config examples/config.json --state .portfolio-agent/demo.sqlite3 status
```

All relative paths resolve from the current working directory. Unknown configuration
keys and out-of-range values fail validation. Do not put credentials in configuration.
JSON output goes to stdout; structured event logs go to stderr. The SQLite audit
is authoritative and survives restarts. State is local and ignored by Git.

## Structure

```text
src/portfolio_agent/
  config.py       Pydantic configuration and JSON loading
  models.py       Task, audit, snapshot, and transition rules
  store.py        StateStore protocol and SQLite transactions
  scheduler.py    Queue, ownership, decisions, retry, and recovery policy
  worker.py       Pure simulated stage execution
  logging.py      Allowlisted JSON events
  cli.py          Local command interface
examples/         Non-secret example configuration
tests/            Deterministic unit and local integration tests
docs/             Architecture, state machine, security, and roadmap
.github/workflows/tests.yml
```

## Implemented and planned

Implemented: typed models, explicit transitions, initial and stage approvals,
priority/FIFO selection, repository-scoped idempotency, single local worker claim,
checkpoint persistence, bounded retries, expired-claim recovery, atomic audit,
JSON logging, CLI simulation, and test CI on Python 3.11–3.13.

Planned: repository discovery and Issue notifications (Phase 2); authorized Issue
commands (3); shared durable storage, automatic dispatch, cross-runner locking,
and robust recovery (4); analysis (5); local LLM adapters and benchmarks (6);
asset approvals (7); modular page generation (8); gallery cards (9); PR publishing
and manual edit preservation (10); private repositories and advanced tooling (11).

Read [architecture](docs/architecture.md), [scheduler and state machine](docs/scheduler.md),
[development and testing](docs/development.md), [security](docs/security.md), and
[roadmap](docs/roadmap.md). This milestone is ready for review before Phase 2.

## License

[MIT](LICENSE). Free to run locally. Future GitHub Actions usage must fit the
account's free allowances; hosted runner capacity and model performance are not
guaranteed. No paid service will be a required dependency.
