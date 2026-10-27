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

## Phase 2 network boundary

Public discovery reads metadata from `api.github.com`; it never clones, reads source
files, executes repository code, or includes descriptions/README content in Issues.
Only `notify --apply` creates Issues. The destination must be public, unarchived,
and Issue-enabled. The agent source and Issue destination are always excluded from
generation candidates. Optional mentions are validated usernames and disabled by
default. Issue comments are untrusted and cannot authorize execution in Phase 2.

The token is read only from `GITHUB_TOKEN`; grant metadata read and Issues read/write
on the destination. Do not store it in configuration or logs. Requests use a fixed
HTTPS host, existing proxy/CA trust, explicit API version, bounded response size,
and a socket timeout. Redirects are disabled to avoid forwarding credentials.
Failures emit fixed safe codes. Listing and Issue delivery use no automatic retries.
CI continues to use mocks with no Issue permission, secrets, or live remote writes.

Keep one durable local database and its backups protected. Notifications reserve
their identity before posting; uncertain outcomes require reconciliation. Never
erase a reservation/database just to retry a POST. Independent databases cannot
provide cross-machine duplicate prevention. Private repositories, automatic
dispatch, authorization of Issue commands, and self-generation remain out of scope.
