"""Deterministic scheduling policy independent of storage and execution."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

from .config import AgentConfig
from .logging import log_event
from .models import TRANSITIONS, AuditEvent, Snapshot, Task, TaskState
from .store import StateStore
from .worker import SimulatedWorker, StageResult


class Scheduler:
    def __init__(
        self,
        store: StateStore,
        config: AgentConfig | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.config = config or AgentConfig()
        self.clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[Snapshot]:
        with self.store.transaction() as snapshot:
            start = len(snapshot.audit)
            yield snapshot
            events = snapshot.audit[start:]
        # Emit only committed events. The database audit remains authoritative.
        for event in events:
            log_event(event)

    def _event(
        self,
        snapshot: Snapshot,
        task: Task,
        event: str,
        actor: str,
        previous: TaskState | None = None,
    ) -> None:
        snapshot.audit.append(
            AuditEvent(
                sequence=len(snapshot.audit),
                timestamp=self.clock(),
                task_id=task.id,
                repository=task.repository,
                actor=actor,
                event=event,
                previous=previous,
                current=task.state,
                checkpoint=task.checkpoint,
                retry_count=task.retries,
            )
        )

    def _transition(
        self,
        snapshot: Snapshot,
        task: Task,
        target: TaskState,
        event: str,
        actor: str = "scheduler",
    ) -> None:
        previous = task.state
        if target not in TRANSITIONS[previous]:
            raise ValueError(f"invalid transition: {previous} -> {target}")
        task.state = target
        if target != TaskState.RUNNING:
            task.owner_token = None
            task.lease_until = None
        self._event(snapshot, task, event, actor, previous)

    def snapshot(self) -> Snapshot:
        with self.store.transaction() as snapshot:
            return snapshot.model_copy(deep=True)

    def enqueue(
        self, repository: str, key: str, *, priority: int = 0, fail_once: bool = False
    ) -> Task:
        with self._transaction() as snapshot:
            for task in snapshot.tasks.values():
                if (
                    task.repository.casefold() == repository.casefold()
                    and task.idempotency_key == key
                ):
                    if task.priority != priority or task.fail_once != fail_once:
                        raise ValueError("idempotency key reused with different options")
                    return task.model_copy(deep=True)
            task = Task(
                id=str(uuid4()),
                repository=repository,
                idempotency_key=key,
                sequence=len(snapshot.tasks),
                priority=priority,
                fail_once=fail_once,
            )
            snapshot.tasks[task.id] = task
            self._event(snapshot, task, "enqueued", "local-user")
            return task.model_copy(deep=True)

    def decide(self, task_id: str, decision: str, actor: str = "local-user") -> Task:
        """Trusted local CLI only; remote caller authorization belongs in Phase 3."""
        with self._transaction() as snapshot:
            task = snapshot.tasks[task_id]
            if decision == "ready" and task.state == TaskState.QUEUED:
                self._transition(snapshot, task, TaskState.READY, "initial_approval", actor)
            elif decision == "approve" and task.state == TaskState.WAITING_FOR_USER:
                self._transition(snapshot, task, TaskState.READY, "stage_approved", actor)
            elif decision == "reject" and task.state in {
                TaskState.QUEUED,
                TaskState.READY,
                TaskState.WAITING_FOR_USER,
                TaskState.RETRY_PENDING,
            }:
                self._transition(snapshot, task, TaskState.CANCELLED, "rejected", actor)
            else:
                raise ValueError(f"cannot {decision} a task in {task.state}")
            return task.model_copy(deep=True)

    def _retry(self, snapshot: Snapshot, task: Task, reason: str) -> None:
        task.last_error = reason  # Fixed safe error codes; never exception text.
        if task.retries >= self.config.max_retries:
            self._transition(snapshot, task, TaskState.FAILED, "retries_exhausted")
            return
        task.retries += 1
        delay = min(
            self.config.retry_cap_seconds, self.config.retry_base_seconds * 2 ** (task.retries - 1)
        )
        task.available_at = self.clock() + delay
        self._transition(snapshot, task, TaskState.RETRY_PENDING, reason)

    def claim(self) -> Task | None:
        """Recover expired claims, promote due retries, and atomically claim one stage."""
        with self._transaction() as snapshot:
            now = self.clock()
            for task in snapshot.tasks.values():
                if task.state == TaskState.RUNNING and (task.lease_until or 0) <= now:
                    self._retry(snapshot, task, "lease_expired")
                if task.state == TaskState.RETRY_PENDING and task.available_at <= now:
                    self._transition(snapshot, task, TaskState.READY, "retry_due")
            if any(task.state == TaskState.RUNNING for task in snapshot.tasks.values()):
                return None
            ready = [task for task in snapshot.tasks.values() if task.state == TaskState.READY]
            if not ready:
                return None
            task = min(ready, key=lambda task: (-task.priority, task.sequence))
            task.owner_token = str(uuid4())
            task.lease_until = now + self.config.lease_seconds
            self._transition(snapshot, task, TaskState.RUNNING, "claimed")
            return task.model_copy(deep=True)

    def finish(self, claim: Task, result: StageResult) -> None:
        with self._transaction() as snapshot:
            task = snapshot.tasks[claim.id]
            if (
                task.state != TaskState.RUNNING
                or task.owner_token != claim.owner_token
                or (task.lease_until or 0) <= self.clock()
            ):
                raise ValueError("stale or expired worker ownership")
            if result.outcome == "failure":
                task.simulated_failure_seen = True
                self._retry(snapshot, task, "worker_failure")
                return
            if result.outcome == "approval" and (
                task.checkpoint >= len(task.stages) or task.stages[task.checkpoint] != "approval"
            ):
                raise ValueError("unexpected approval result")
            if task.checkpoint < len(task.stages):
                stage = task.stages[task.checkpoint]
                if stage == "approval" and result.outcome != "approval":
                    raise ValueError("approval stage cannot be skipped")
                if result.output is not None:
                    task.outputs[f"{task.checkpoint}:{stage}"] = result.output
                task.checkpoint += 1
            task.last_error = None
            target = (
                TaskState.WAITING_FOR_USER
                if result.outcome == "approval"
                else TaskState.COMPLETED
                if task.checkpoint == len(task.stages)
                else TaskState.READY
            )
            self._transition(snapshot, task, target, "stage_checkpointed")

    def tick(self, worker: SimulatedWorker | None = None) -> bool:
        task = self.claim()
        if task is None:
            return False
        try:
            result = (worker or SimulatedWorker()).execute(task)
        except Exception:
            result = StageResult("failure")
        self.finish(task, result)
        return True

    def run(self, max_steps: int = 100, monotonic: Callable[[], float] = time.monotonic) -> int:
        """Drain eligible stages, stopping at idle, step limit, or budget. Never sleep."""
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        deadline = monotonic() + self.config.job_budget_seconds
        steps = 0
        while steps < max_steps and monotonic() < deadline and self.tick():
            steps += 1
        return steps
