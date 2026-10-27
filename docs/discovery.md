# Phase 2: public discovery and approval Issues

Phase 2 lists public repositories (including repositories with no commits), saves
a local registry, and creates one approval Issue per repository ID and destination.
It does not enqueue tasks, approve execution, parse Issue commands, analyze code,
or generate/publish pages. Those integrations belong to later phases.

## Configure and preview

Copy `examples/discovery.json` to `discovery.local.json` and edit it. Replace
`YOUR-ACCOUNT` with the GitHub user or organization to discover. Set `account_type`
to `user` or `organization`. Choose the public, Issue-enabled destination repository,
the agent's source repository, and full `owner/name` exclusions. The example excludes
the portfolio website. The source and destination repositories are always excluded
from generation candidates; Phase 2 does not permit self-generation. Forks and
archived repositories are excluded unless explicitly enabled. Private repositories
and other accounts are never registered, even if credentials could access them.

`mentions` is an explicit list of usernames without `@`; its default is empty.
Creating an Issue can still notify watchers/subscribers under GitHub's normal
notification settings. Inspect the preview before enabling external writes.

Run from the repository root after installing the package:

```bash
portfolio-agent --config discovery.local.json discover
portfolio-agent --config discovery.local.json registry
portfolio-agent --config discovery.local.json notify
```

`discover` reads metadata from GitHub and updates SQLite; it does not create Issues.
`notify` without `--apply` only reads local state and prints the intended Issue titles,
bodies, exclusions, and previous notification state. It needs no token or network.
Public discovery can run without a token, subject to GitHub's lower anonymous limits.
Use `python -m portfolio_agent` instead of `portfolio-agent` if preferred. In Windows
PowerShell without activation, use `.\.venv\Scripts\python.exe -m portfolio_agent`.

Use the same state path on every invocation. Relative paths are resolved from the
current directory. Back up existing SQLite data before adopting new versions.
Phase 2 adds independent versioned tables without rewriting the Phase 1 snapshot.

## Enable Issue creation deliberately

Provide `GITHUB_TOKEN` through your shell/session secret mechanism. Never put it in
JSON configuration, source files, command-line arguments, or checked-in files. A
fine-grained token needs repository metadata read and Issues read/write for the
chosen destination. No Contents write, Actions write, administration, or model API
credentials are required. Organization token policies may require extra approval.

After confirming the account, exclusions, destination, and notification behavior:

```bash
portfolio-agent --config discovery.local.json notify --apply
portfolio-agent --config discovery.local.json registry
portfolio-agent --config discovery.local.json registry --audit
```

The adapter checks that the destination is public, unarchived, and Issue-enabled,
then lists **all** destination Issues, including closed Issues. Pull requests are
ignored. A machine marker on its own line identifies each source by stable numeric
repository ID, so renames and repeated scans do not request another approval.
Closed/deleted/edited Issues already linked locally are never automatically replaced.
Current exclusions are checked before every delivery; a repository absent from the
latest successful scan stays in history but cannot receive a new notification.

Issue descriptions explicitly say that generation has not been approved. Phase 3
will add authorized Issue commands; no comment currently changes task state.

## Failures and duplicate prevention

Local SQLite transactions serialize reservations across processes sharing the same
database. The delivery intent commits **before** the POST. After a successful reply,
the Issue number and an audit event commit together. On a timeout or crash, the
record remains `uncertain`; subsequent runs reconcile the marker against GitHub.
They never automatically repeat that POST. This trades automatic retry for avoiding
duplicate notifications, even when a remote result is temporarily invisible.

If an entry stays `uncertain`, inspect the destination Issues and wait for any
outstanding invocation to finish. Run `notify --apply` again to reconcile. If no
Issue exists, manually create one using the exact preview title/body (including
the marker), then rerun reconciliation. Do not delete the database or reservations
to retry: that discards the local evidence preventing duplicate delivery.

Use one durable local database for each installation. Separate machines/databases
cannot coordinate creation, so do not run concurrent installations against the
same destination. Fresh registries reconcile existing marked Issues, but this is
not a distributed exactly-once delivery guarantee. Shared storage and distributed
recovery remain Phase 4 work. No scheduled workflow or background dispatcher is
introduced; invoke discovery and notification explicitly.

HTTP calls have a 30-second socket timeout, a 4 MB response limit, and a maximum of
1,000 pages of 100 records per listing. Redirects and automatic POST retries are
disabled. A failed/incomplete discovery listing never replaces the previous scan.
Authentication, access, rate-limit, and network failures return safe codes and exit
2 without printing tokens, response bodies, raw exceptions, or repository content.
Fix access or wait for the rate limit, then rerun; the CLI never sleeps for GitHub.

Zero-size metadata is retained, not interpreted as proof of an empty repository:
discovery does not inspect commits or files. The registry stores only public
metadata needed for discovery and notification, plus local delivery/audit records.
