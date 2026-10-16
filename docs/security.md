# Security and permissions

Phase 1 requires no GitHub tokens, secrets, API calls, repository clones, or
LLM inference. Simulated jobs never execute repository code, subprocesses,
HTML, or JavaScript. Repository identifiers are validated as `owner/name`.
Configuration rejects unknown fields. Treat the state database as trusted local
application data, keep it outside version control, and protect its directory
with operating-system permissions. It is neither encrypted nor tamper-evident.
Backups and SQLite journal files need the same access controls.

Only someone with access to the local process/database can make decisions today.
Do not expose the CLI as a remote endpoint. The actor field is not authorization.
Future GitHub commands must authenticate webhook/event provenance and authorize
a narrowly configured allowlist before accepting any state-changing command.
Reject untrusted Issue content as instructions to execution tools.

CI uses `contents: read`, disables checkout credential persistence, runs on
standard Ubuntu runners, and never requests secrets. It does not use
`pull_request_target`, create Issues, push branches, or deploy. Future scheduled
jobs must keep discovery permissions separate from generation and publishing.
A GitHub App with narrow repository permissions is planned for private content;
its exact permission matrix must be designed with those adapters.

Future safeguards: bounded repository reads; secret and binary exclusion;
structured, schema-validated LLM outputs; sandboxed output validation; explicit
asset and publishing approvals; public-content sanitization for private source;
manual-edit hash/conflict detection; and PR-only publication. These are design
requirements, not features implemented in Phase 1. The self-generation operation
must require explicit approval and filter its own generated commits/events to
prevent recursive triggers.
