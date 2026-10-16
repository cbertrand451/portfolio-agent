"""Pure, deterministic simulated execution; no network, subprocesses, or LLMs."""

from dataclasses import dataclass
from typing import Literal

from .models import Task


@dataclass(frozen=True)
class StageResult:
    outcome: Literal["completed", "approval", "failure"]
    output: str | None = None


class SimulatedWorker:
    def execute(self, task: Task) -> StageResult:
        if task.fail_once and not task.simulated_failure_seen:
            return StageResult("failure")
        if task.checkpoint == len(task.stages):
            return StageResult("completed")
        stage = task.stages[task.checkpoint]
        if stage == "approval":
            return StageResult("approval")
        return StageResult("completed", f"simulated:{stage}:{task.repository}")
