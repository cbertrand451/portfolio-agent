"""Transactional local persistence behind an interchangeable state-store contract."""

import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from pathlib import Path
from typing import Protocol

from .models import Snapshot


class StateStore(Protocol):
    """Transactions serialize read/modify/write; exceptions roll back all changes.

    Implementations must give callers an isolated snapshot and commit atomically.
    """

    def transaction(self) -> AbstractContextManager[Snapshot]: ...


class SQLiteStateStore:
    """Single-machine prototype: one versioned JSON snapshot in SQLite.

    BEGIN IMMEDIATE prevents lost updates between independent local processes.
    Never place this database on a network share or treat runner disks as durable.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS state "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO state VALUES (1, ?)", (Snapshot().model_dump_json(),)
            )

    @contextmanager
    def transaction(self) -> Iterator[Snapshot]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM state WHERE id = 1").fetchone()
            if row is None:
                raise ValueError("missing state snapshot")
            snapshot = Snapshot.model_validate_json(row[0])
            yield snapshot
            # Revalidate nested mutations before persisting; never reset corrupt state silently.
            validated = Snapshot.model_validate(snapshot.model_dump())
            connection.execute(
                "UPDATE state SET payload = ? WHERE id = 1", (validated.model_dump_json(),)
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
