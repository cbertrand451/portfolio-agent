# Milestones and review gates

| Phase | Deliverable | Status |
| --- | --- | --- |
| 1 | Local scaffold, architecture, typed configuration/state, simulated scheduler, transactional store, tests, CLI, CI | Implemented for review |
| 2 | Public repository discovery (including empty repositories), durable registry, duplicate-safe approval Issues | Implemented for review |
| 3 | Deterministic Issue commands, authorized users, decisions and audit | Planned |
| 4 | Shared durable queue, distributed locking, checkpoint artifacts, dispatch/reconciliation, lost-wakeup protection | Planned |
| 5 | Deterministic bounded repository analysis, no code execution | Planned |
| 6 | Ollama/llama.cpp/mock provider contract, schemas, compact model benchmarks | Planned |
| 7 | Design proposals, visual assets, preview and explicit approvals | Planned |
| 8 | Modular accessible HTML/CSS/JS, component map and documentation | Planned |
| 9 | Separate gallery card package and independent update contract | Planned |
| 10 | Review branches/PRs, output contract, update conflicts and manual edit preservation | Planned |
| 11 | GitHub App private access, sanitization, Playwright, advanced features | Planned |

Before Phase 2, review the local state semantics and CLI behavior. Confirm the
account and destination repository for discovery, repository exclusions (including
self-generation policy), and intended Issue notification/mention behavior before
performing external writes. No such decisions block this offline milestone.

Before real inference, benchmark free models on standard hosted runner CPU/RAM,
quality, validity, and download/runtime budgets. Zero subscription cost does not
guarantee practical model latency or unlimited free runner minutes. Choose model
licenses explicitly and avoid unnecessary downloads. No model is selected or
installed in Phase 1.
