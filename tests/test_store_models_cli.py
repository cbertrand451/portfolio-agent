import json
import sqlite3

import pytest
from pydantic import ValidationError

from portfolio_agent.cli import main
from portfolio_agent.config import AgentConfig
from portfolio_agent.models import TRANSITIONS, Snapshot, Task, TaskState
from portfolio_agent.store import SQLiteStateStore


def test_store_commit_restart_and_rollback(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStateStore(path)
    with store.transaction() as state:
        state.tasks["task"] = Task(
            id="task", repository="owner/repo", idempotency_key="key", sequence=0
        )
    with pytest.raises(RuntimeError), store.transaction() as state:
        state.tasks.clear()
        raise RuntimeError("abort")
    with SQLiteStateStore(path).transaction() as state:
        assert list(state.tasks) == ["task"]


def test_unknown_schema_fails_closed(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStateStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE state SET payload = ?", ('{"schema_version": 2}',))
    with pytest.raises(ValidationError), store.transaction():
        pass
    with sqlite3.connect(path) as connection:
        assert json.loads(connection.execute("SELECT payload FROM state").fetchone()[0]) == {
            "schema_version": 2
        }


@pytest.mark.parametrize(
    "data",
    [{"max_retries": -1}, {"unknown": True}, {"lease_seconds": 0}, {"job_budget_seconds": 0}],
)
def test_invalid_configuration(data):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(data)


def test_config_file_and_checkpoint_validation(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"max_retries": 0}')
    assert AgentConfig.load(path).max_retries == 0
    with pytest.raises(ValidationError):
        Task(id="id", repository="invalid", idempotency_key="key", sequence=0)
    with pytest.raises(ValidationError):
        Task(id="id", repository="owner/repo", idempotency_key="key", sequence=0, checkpoint=4)
    assert Snapshot().schema_version == 1


def test_transition_graph_covers_all_states_and_terminal_states():
    assert set(TRANSITIONS) == set(TaskState)
    for state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
        assert TRANSITIONS[state] == frozenset()
    assert TaskState.RUNNING not in TRANSITIONS[TaskState.WAITING_FOR_USER]


def test_cli_full_lifecycle(tmp_path, capsys):
    options = ["--state", str(tmp_path / "state.sqlite3")]
    assert main([*options, "enqueue", "owner/project", "--key", "initial"]) == 0
    task_id = json.loads(capsys.readouterr().out)["id"]
    for command in ("ready", "approve"):
        main([*options, command, task_id])
        capsys.readouterr()
        main([*options, "run"])
        capsys.readouterr()
    main([*options, "status"])
    assert json.loads(capsys.readouterr().out)[0]["state"] == "COMPLETED"
    main([*options, "audit"])
    assert json.loads(capsys.readouterr().out)[-1]["current"] == "COMPLETED"
    with pytest.raises(SystemExit) as error:
        main([*options, "approve", task_id])
    assert error.value.code == 2


def test_inconsistent_ownership_rolls_back(scheduler):
    task = scheduler.enqueue("owner/repo", "key")
    with (
        pytest.raises(ValidationError, match="only running tasks"),
        scheduler.store.transaction() as state,
    ):
        state.tasks[task.id].owner_token = "orphaned-owner"
    assert scheduler.snapshot().tasks[task.id].owner_token is None


def test_duplicate_running_state_fails_closed():
    tasks = {
        key: Task(
            id=key,
            repository=f"owner/{key}",
            idempotency_key="key",
            sequence=index,
            state=TaskState.RUNNING,
            owner_token=key,
            lease_until=100,
        )
        for index, key in enumerate(("one", "two"))
    }
    with pytest.raises(ValidationError, match="only one task"):
        Snapshot(tasks=tasks)
