"""Validated state and the complete Phase 1 transition graph."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskState(StrEnum):
    QUEUED = "QUEUED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    CANCELLED = "CANCELLED"


TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.READY,
            TaskState.WAITING_FOR_USER,
            TaskState.COMPLETED,
            TaskState.RETRY_PENDING,
            TaskState.FAILED,
        }
    ),
    TaskState.WAITING_FOR_USER: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.RETRY_PENDING: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.FAILED: frozenset(),
    TaskState.COMPLETED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class Task(Model):
    id: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0)
    priority: int = Field(default=0, ge=-100, le=100)
    state: TaskState = TaskState.QUEUED
    stages: list[Literal["analyze", "approval", "render"]] = Field(
        default_factory=lambda: ["analyze", "approval", "render"], min_length=1
    )
    checkpoint: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    available_at: float = 0
    owner_token: str | None = None
    lease_until: float | None = None
    fail_once: bool = False
    simulated_failure_seen: bool = False
    last_error: str | None = None
    # Only deterministic, non-sensitive simulation output is persisted in Phase 1.
    outputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def checkpoint_in_range(self) -> "Task":
        if self.checkpoint > len(self.stages):
            raise ValueError("checkpoint exceeds stage count")
        return self


class AuditEvent(Model):
    sequence: int
    timestamp: float
    task_id: str
    repository: str
    actor: str
    event: str
    previous: TaskState | None
    current: TaskState
    checkpoint: int
    retry_count: int


class Snapshot(Model):
    schema_version: Literal[1] = 1
    tasks: dict[str, Task] = Field(default_factory=dict)
    audit: list[AuditEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_ownership(self) -> "Snapshot":
        running = 0
        identities = set()
        for task_id, task in self.tasks.items():
            if task_id != task.id:
                raise ValueError("task dictionary key must match its ID")
            identity = (task.repository.casefold(), task.idempotency_key)
            if identity in identities:
                raise ValueError("duplicate repository idempotency key")
            identities.add(identity)
            if task.state == TaskState.RUNNING:
                running += 1
                if not task.owner_token or task.lease_until is None:
                    raise ValueError("running task requires ownership and lease")
            elif task.owner_token is not None or task.lease_until is not None:
                raise ValueError("only running tasks can hold ownership")
        if running > 1:
            raise ValueError("only one task may be running")
        return self
