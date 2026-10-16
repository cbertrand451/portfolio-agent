from dataclasses import dataclass

import pytest

from portfolio_agent.config import AgentConfig
from portfolio_agent.scheduler import Scheduler
from portfolio_agent.store import SQLiteStateStore


@dataclass
class Clock:
    now: float = 1000

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def scheduler(tmp_path, clock):
    config = AgentConfig(
        state_path=tmp_path / "state.sqlite3",
        retry_base_seconds=2,
        retry_cap_seconds=3,
        lease_seconds=10,
        max_retries=2,
    )
    return Scheduler(SQLiteStateStore(config.state_path), config, clock)
