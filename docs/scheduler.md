# Scheduler and state machine

## Explicit transitions

| Source | Allowed destinations | Cause |
| --- | --- | --- |
| QUEUED | READY, CANCELLED | initial approval or rejection |
| READY | RUNNING, CANCELLED | atomic claim or rejection |
| RUNNING | READY | successful intermediate stage |
| RUNNING | WAITING_FOR_USER | checkpointed approval request |
| RUNNING | COMPLETED | final stage succeeds |
| RUNNING | RETRY_PENDING, FAILED | failure or expired lease |
| WAITING_FOR_USER | READY, CANCELLED | approval or rejection |
| RETRY_PENDING | READY, CANCELLED | backoff becomes due or rejection |
| COMPLETED, FAILED, CANCELLED | none | terminal |

`models.TRANSITIONS` is authoritative; `_transition` rejects all other edges.
The application additionally restricts decisions by context, so `ready` cannot
bypass an existing stage approval and `approve` cannot skip initial approval.
Approving a waiting task while another task runs never changes the running claim.

## Selection, idempotency, and isolation

The identity of an enqueue request is `(case-insensitive repository, key)`.
Repeated enqueue returns the existing task even after completion; incompatible
options with the same key fail. A new key creates a new isolated task, even for
the same repository. No task reads another task's checkpoints or outputs.

Every claim transaction first recovers expired RUNNING tasks and promotes due
retries. If a live RUNNING task exists it returns no claim. Otherwise it selects
`(-priority, enqueue_sequence)`, creates a fresh owner token, and records a lease.
The worker receives a detached copy, so it cannot mutate durable state directly.

Completion checks task state, owner token, and expiry before any output writes.
Completion and checkpoint updates share the same transaction as their audit.
A replayed or expired completion is rejected. Workers must finish within the
lease in this prototype; no heartbeat is implemented.

## Recovery and retries

A failure leaves the checkpoint unchanged. A crash after claim leaves RUNNING
persisted. Before the lease expires, new schedulers respect it. On the next claim
after expiry, the scheduler records `lease_expired`, releases ownership, and
enters RETRY_PENDING. Recovery consumes the same finite retry budget as failures.

Retry number `n` waits `min(cap, base * 2 ** (n - 1))` seconds. When due, it becomes
READY on a subsequent `run`. The previous committed checkpoint resumes. A crash
before the completion transaction commits may repeat the stage; simulation has
no external side effects. Real workers must use idempotent artifact operations
and stale-owner fencing before this can be used for generation.

The CLI never waits for humans or retry deadlines. Automatic dispatch, scheduled
reconciliation, transactional wakeup records, and lost-wakeup protection across
runner exits are **Phase 4 requirements**, not present guarantees. Local users
explicitly invoke `run` after a state change. The in-process loop drains available
work immediately, including other repositories after approval waits.

## Audit and observability

Events record task ID, repository, actor, timestamp (UTC Unix seconds), old/new
state, checkpoint, retry count, event name, and a monotonic audit sequence. Task
records expose owner/expiry, output, retry deadline, and fixed safe error codes.
No raw exceptions or analyzed content are logged. Model/runtime metrics and final
artifact locations will be added when those stages exist. Local actor strings are
audit labels; they do not establish authenticated identity.
