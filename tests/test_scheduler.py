from concurrent.futures import ThreadPoolExecutor

import pytest

from portfolio_agent.models import TaskState
from portfolio_agent.scheduler import Scheduler
from portfolio_agent.store import SQLiteStateStore
from portfolio_agent.worker import SimulatedWorker, StageResult


def ready(scheduler, repository="owner/project", key="initial", **kwargs):
    task = scheduler.enqueue(repository, key, **kwargs)
    scheduler.decide(task.id, "ready")
    return task


def test_enqueue_is_idempotent_and_requires_initial_approval(scheduler):
    task = scheduler.enqueue("Owner/project", "initial")
    duplicate = scheduler.enqueue("owner/project", "initial")
    assert task.id == duplicate.id
    assert scheduler.claim() is None
    assert len(scheduler.snapshot().audit) == 1
    with pytest.raises(ValueError, match="different options"):
        scheduler.enqueue("owner/project", "initial", priority=1)
    assert scheduler.enqueue("owner/other", "initial").id != task.id


def test_priority_then_fifo_and_global_ownership(scheduler):
    low = ready(scheduler, "owner/low")
    first = ready(scheduler, "owner/first", priority=2)
    ready(scheduler, "owner/second", priority=2)
    claim = scheduler.claim()
    assert claim.id == first.id
    assert scheduler.claim() is None
    scheduler.finish(claim, SimulatedWorker().execute(claim))
    assert scheduler.claim().id == first.id  # FIFO is by original enqueue order.
    assert scheduler.snapshot().tasks[low.id].state == TaskState.READY


def test_wait_releases_worker_and_approval_does_not_interrupt(scheduler):
    first = ready(scheduler)
    assert scheduler.run() == 2
    waiting = scheduler.snapshot().tasks[first.id]
    assert waiting.state == TaskState.WAITING_FOR_USER
    assert waiting.owner_token is None
    assert waiting.checkpoint == 2
    second = ready(scheduler, "owner/second")
    claim = scheduler.claim()
    assert claim.id == second.id
    scheduler.decide(first.id, "approve")
    assert scheduler.claim() is None
    scheduler.finish(claim, SimulatedWorker().execute(claim))
    assert scheduler.run() == 2  # First completes; second reaches its approval wait.
    completed = scheduler.snapshot().tasks[first.id]
    assert completed.state == TaskState.COMPLETED
    assert completed.outputs == {
        "0:analyze": "simulated:analyze:owner/project",
        "2:render": "simulated:render:owner/project",
    }


def test_retry_backoff_and_resume_without_sleep(scheduler, clock):
    task = ready(scheduler, fail_once=True)
    assert scheduler.run() == 1
    pending = scheduler.snapshot().tasks[task.id]
    assert pending.state == TaskState.RETRY_PENDING
    assert pending.available_at == clock.now + 2
    assert pending.checkpoint == 0
    assert scheduler.run() == 0
    clock.advance(2)
    assert scheduler.run() == 2
    assert scheduler.snapshot().tasks[task.id].state == TaskState.WAITING_FOR_USER


def test_bounded_retries_cap_delay_and_stop(scheduler, clock):
    task = ready(scheduler)
    for expected_retries, delay in ((1, 2), (2, 3)):
        claim = scheduler.claim()
        scheduler.finish(claim, StageResult("failure"))
        pending = scheduler.snapshot().tasks[task.id]
        assert pending.retries == expected_retries
        assert pending.available_at == clock.now + delay
        clock.advance(delay)
    scheduler.finish(scheduler.claim(), StageResult("failure"))
    assert scheduler.snapshot().tasks[task.id].state == TaskState.FAILED
    assert scheduler.claim() is None


def test_restart_recovery_preserves_checkpoint_and_fences_old_claim(scheduler, clock):
    task = ready(scheduler)
    scheduler.tick()  # completed analyze
    stale = scheduler.claim()
    restarted = Scheduler(SQLiteStateStore(scheduler.config.state_path), scheduler.config, clock)
    assert restarted.claim() is None
    clock.advance(10)
    assert restarted.claim() is None  # recovery enters backoff
    with pytest.raises(ValueError, match="stale"):
        scheduler.finish(stale, SimulatedWorker().execute(stale))
    clock.advance(2)
    recovered = restarted.claim()
    assert recovered.id == task.id
    assert recovered.checkpoint == 1
    assert recovered.owner_token != stale.owner_token
    restarted.finish(recovered, SimulatedWorker().execute(recovered))
    restarted.decide(task.id, "approve")
    restarted.run()
    assert restarted.snapshot().tasks[task.id].state == TaskState.COMPLETED
    assert "lease_expired" in [event.event for event in restarted.snapshot().audit]


def test_expired_claim_cannot_commit_even_before_recovery(scheduler, clock):
    ready(scheduler)
    claim = scheduler.claim()
    clock.advance(10)
    with pytest.raises(ValueError, match="expired"):
        scheduler.finish(claim, SimulatedWorker().execute(claim))


def test_competing_schedulers_claim_only_one(scheduler, clock):
    ready(scheduler)
    ready(scheduler, "owner/second")
    other = Scheduler(SQLiteStateStore(scheduler.config.state_path), scheduler.config, clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda instance: instance.claim(), [scheduler, other]))
    assert sum(claim is not None for claim in claims) == 1


def test_duplicate_concurrent_enqueue(scheduler, clock):
    other = Scheduler(SQLiteStateStore(scheduler.config.state_path), scheduler.config, clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(
            pool.map(lambda instance: instance.enqueue("owner/repo", "same"), [scheduler, other])
        )
    assert tasks[0].id == tasks[1].id
    assert len(scheduler.snapshot().tasks) == 1


def test_rejection_and_invalid_decision_roll_back(scheduler):
    task = scheduler.enqueue("owner/project", "initial")
    before = scheduler.snapshot()
    with pytest.raises(ValueError):
        scheduler.decide(task.id, "approve")
    assert scheduler.snapshot() == before
    scheduler.decide(task.id, "reject")
    assert scheduler.snapshot().tasks[task.id].state == TaskState.CANCELLED
    assert scheduler.run() == 0


def test_approval_gate_cannot_be_skipped(scheduler):
    task = ready(scheduler)
    scheduler.tick()
    claim = scheduler.claim()
    with pytest.raises(ValueError, match="cannot be skipped"):
        scheduler.finish(claim, StageResult("completed"))
    assert scheduler.snapshot().tasks[task.id].checkpoint == 1


def test_budget_and_step_limits_checkpoint(scheduler):
    task = ready(scheduler)
    times = iter([0, 0, 6000])
    assert scheduler.run(monotonic=lambda: next(times)) == 1
    assert scheduler.snapshot().tasks[task.id].checkpoint == 1
    assert scheduler.run(max_steps=1) == 1
    assert scheduler.snapshot().tasks[task.id].state == TaskState.WAITING_FOR_USER
    with pytest.raises(ValueError):
        scheduler.run(max_steps=0)


def test_exception_is_safe_retry_without_logging_secrets(scheduler, caplog):
    class BrokenWorker:
        def execute(self, task):
            raise RuntimeError("secret-token-never-log")

    task = ready(scheduler)
    scheduler.tick(BrokenWorker())
    assert scheduler.snapshot().tasks[task.id].last_error == "worker_failure"
    assert "secret-token-never-log" not in scheduler.snapshot().model_dump_json()
    assert "secret-token-never-log" not in caplog.text


def test_audit_sequence_and_actor(scheduler):
    task = ready(scheduler)
    scheduler.run()
    scheduler.decide(task.id, "approve", actor="test-user")
    scheduler.run()
    audit = scheduler.snapshot().audit
    assert [event.sequence for event in audit] == list(range(len(audit)))
    assert any(event.actor == "test-user" and event.event == "stage_approved" for event in audit)
    assert audit[-1].current == TaskState.COMPLETED
