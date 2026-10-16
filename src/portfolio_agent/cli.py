"""Local simulation commands. JSON goes to stdout; event logs go to stderr."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from .config import AgentConfig
from .logging import configure_logging
from .scheduler import Scheduler
from .store import SQLiteStateStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Portfolio Agent: local Phase 1 simulation")
    root.add_argument("--config", type=Path, help="JSON configuration file")
    root.add_argument("--state", type=Path, help="override the local SQLite path")
    commands = root.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue", help="register a task; does not approve execution")
    enqueue.add_argument("repository", help="owner/repository")
    enqueue.add_argument("--key", required=True, help="per-repository idempotency key")
    enqueue.add_argument("--priority", type=int, default=0)
    enqueue.add_argument("--fail-once", action="store_true")
    for name in ("ready", "approve", "reject"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
    run = commands.add_parser(
        "run", help="run eligible simulated stages until idle or bounded limit"
    )
    run.add_argument("--max-steps", type=int, default=100)
    commands.add_parser("status")
    commands.add_parser("audit")
    return root


def main(argv: list[str] | None = None) -> int:
    root = parser()
    args = root.parse_args(argv)
    configure_logging()
    try:
        config = AgentConfig.load(args.config)
        if args.state is not None:
            config = AgentConfig.model_validate({**config.model_dump(), "state_path": args.state})
        scheduler = Scheduler(SQLiteStateStore(config.state_path), config)
        if args.command == "enqueue":
            result = scheduler.enqueue(
                args.repository, args.key, priority=args.priority, fail_once=args.fail_once
            ).model_dump(mode="json")
        elif args.command in {"ready", "approve", "reject"}:
            result = scheduler.decide(args.task_id, args.command).model_dump(mode="json")
        elif args.command == "run":
            result = {"steps": scheduler.run(args.max_steps)}
        elif args.command == "status":
            result = [task.model_dump(mode="json") for task in scheduler.snapshot().tasks.values()]
        else:
            result = [event.model_dump(mode="json") for event in scheduler.snapshot().audit]
        print(json.dumps(result, indent=2))
    except (ValidationError, ValueError, KeyError, OSError, sqlite3.Error):
        # Validation errors may contain untrusted input. Keep CLI failures bounded and safe.
        root.exit(
            2, "error: invalid input, task state, or state storage; see usage and documentation\n"
        )
    return 0
