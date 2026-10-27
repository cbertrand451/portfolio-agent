"""Public discovery and conservative Issue delivery, separate from task approvals."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .config import DiscoveryConfig
from .github import Issue, PublicRepository


class DiscoveryAPI(Protocol):
    def repositories(self, account: str, account_type: str) -> list[PublicRepository]: ...

    def issues(self, repository: str) -> list[Issue]: ...

    def check_destination(self, repository: str) -> None: ...

    def create_issue(self, repository: str, title: str, body: str) -> Issue: ...


class Registry:
    """Additive tables in the local database; Phase 1 snapshots stay at schema 1."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS discovery_schema (version INTEGER NOT NULL)")
            versions = db.execute("SELECT version FROM discovery_schema").fetchall()
            if not versions:
                db.execute("INSERT INTO discovery_schema VALUES (1)")
            elif len(versions) != 1 or versions[0][0] != 1:
                raise ValueError("unsupported registry schema")
            db.execute(
                "CREATE TABLE IF NOT EXISTS repositories "
                "(id INTEGER PRIMARY KEY, account TEXT NOT NULL, payload TEXT NOT NULL, "
                "present INTEGER NOT NULL, observed_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS notifications "
                "(repository_id INTEGER NOT NULL, destination TEXT NOT NULL, "
                "status TEXT NOT NULL, issue_number INTEGER, "
                "PRIMARY KEY (repository_id, destination))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS discovery_audit "
                "(sequence INTEGER PRIMARY KEY, timestamp REAL NOT NULL, "
                "event TEXT NOT NULL, repository_id INTEGER, destination TEXT)"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, event, repository_id=None, destination=None):
        db.execute(
            "INSERT INTO discovery_audit(timestamp,event,repository_id,destination) "
            "VALUES(?,?,?,?)",
            (time.time(), event, repository_id, destination),
        )

    def observe(self, account: str, repositories: list[PublicRepository]):
        # A failed/partial API scan never removes previously observed repositories.
        with self.transaction() as db:
            db.execute("UPDATE repositories SET present=0 WHERE account=?", (account.casefold(),))
            for repository in repositories:
                db.execute(
                    "INSERT INTO repositories VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "account=excluded.account,payload=excluded.payload,present=1,"
                    "observed_at=excluded.observed_at",
                    (
                        repository.id,
                        account.casefold(),
                        repository.model_dump_json(),
                        1,
                        time.time(),
                    ),
                )
            self.audit(db, "discovery_completed")

    def records(self) -> list[dict]:
        with self.transaction() as db:
            result = []
            for row in db.execute("SELECT * FROM repositories ORDER BY id"):
                repository = PublicRepository.model_validate_json(row["payload"])
                notifications = [
                    dict(item)
                    for item in db.execute(
                        "SELECT destination,status,issue_number FROM notifications "
                        "WHERE repository_id=? ORDER BY destination",
                        (repository.id,),
                    )
                ]
                result.append(
                    {
                        **repository.model_dump(),
                        "present": bool(row["present"]),
                        "observed_at": row["observed_at"],
                        "notifications": notifications,
                    }
                )
            return result

    def events(self) -> list[dict]:
        with self.transaction() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM discovery_audit ORDER BY sequence")
            ]


def exclusion(repository: PublicRepository, config: DiscoveryConfig) -> str | None:
    name = repository.full_name.casefold()
    if repository.private:
        return "private"
    if name.split("/")[0] != config.account.casefold():
        return "different_account"
    # Self-generation and the Issue destination are excluded in Phase 2.
    if name in {config.source_repository.casefold(), config.issue_repository.casefold()}:
        return "agent_or_destination"
    if name in {item.casefold() for item in config.exclude_repositories}:
        return "configured_exclusion"
    if repository.fork and not config.include_forks:
        return "fork"
    if repository.archived and not config.include_archived:
        return "archived"
    return None


def marker(repository_id: int) -> str:
    return f"<!-- portfolio-agent:repository:{repository_id} -->"


class Discovery:
    def __init__(self, registry: Registry, api: DiscoveryAPI, config: DiscoveryConfig):
        self.registry, self.api, self.config = registry, api, config

    def scan(self) -> list[dict]:
        # Metadata listing includes zero-size/empty repositories; no commit API is needed.
        repositories = self.api.repositories(self.config.account, self.config.account_type)
        public = {
            repo.id: repo
            for repo in repositories
            if not repo.private
            and repo.full_name.split("/")[0].casefold() == self.config.account.casefold()
        }
        self.registry.observe(self.config.account, list(public.values()))
        return self.plan()

    def plan(self) -> list[dict]:
        result = []
        for record in self.registry.records():
            repo = PublicRepository.model_validate(record)
            if repo.full_name.split("/")[0].casefold() != self.config.account.casefold():
                continue
            reason = exclusion(repo, self.config) if record["present"] else "not_in_latest_scan"
            title = f"Portfolio approval: {repo.full_name}"
            mentions = " ".join(f"@{name}" for name in self.config.mentions)
            body = (
                f"{marker(repo.id)}\n\n"
                f"Review [{repo.full_name}](https://github.com/{repo.full_name}) "
                "for a future portfolio case study.\n\n"
                "This request does not approve or start generation. Issue commands "
                "are not implemented yet (Phase 3).\n"
            )
            if mentions:
                body += f"\nRequested reviewers: {mentions}\n"
            result.append(
                {
                    "repository_id": repo.id,
                    "repository": repo.full_name,
                    "excluded_reason": reason,
                    "destination": self.config.issue_repository,
                    "title": title,
                    "body": body,
                    "notifications": record["notifications"],
                }
            )
        return result

    def notify(self) -> list[dict]:
        plans = [plan for plan in self.plan() if plan["excluded_reason"] is None]
        if not plans:
            return []
        self.api.check_destination(self.config.issue_repository)
        # List all states via the Issues endpoint (not eventually indexed search).
        issues = self.api.issues(self.config.issue_repository)
        destination = self.config.issue_repository.casefold()
        result = []
        for plan in plans:
            repo_id = plan["repository_id"]
            matches = [
                issue
                for issue in issues
                if issue.pull_request is None and marker(repo_id) in (issue.body or "").splitlines()
            ]
            with self.registry.transaction() as db:
                existing = db.execute(
                    "SELECT status,issue_number FROM notifications "
                    "WHERE repository_id=? AND destination=?",
                    (repo_id, destination),
                ).fetchone()
                if matches:
                    number = min(issue.number for issue in matches)
                    db.execute(
                        "INSERT INTO notifications VALUES(?,?,?,?) "
                        "ON CONFLICT(repository_id,destination) DO UPDATE SET "
                        "status=excluded.status,issue_number=excluded.issue_number",
                        (repo_id, destination, "linked", number),
                    )
                    self.registry.audit(db, "issue_reconciled", repo_id, destination)
                    result.append(
                        {"repository_id": repo_id, "status": "linked", "issue_number": number}
                    )
                    continue
                if existing:
                    # A committed intent may represent an accepted POST whose response was lost.
                    # Never POST again automatically, even if an immediate GET cannot find it.
                    result.append({"repository_id": repo_id, **dict(existing)})
                    continue
                db.execute(
                    "INSERT INTO notifications VALUES(?,?,?,NULL)",
                    (repo_id, destination, "uncertain"),
                )
                self.registry.audit(db, "issue_create_reserved", repo_id, destination)
            # Intent is durable before touching the remote API, including before a process crash.
            issue = self.api.create_issue(self.config.issue_repository, plan["title"], plan["body"])
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE notifications SET status='linked',issue_number=? "
                    "WHERE repository_id=? AND destination=?",
                    (issue.number, repo_id, destination),
                )
                self.registry.audit(db, "issue_created", repo_id, destination)
            issues.append(issue)
            result.append(
                {"repository_id": repo_id, "status": "linked", "issue_number": issue.number}
            )
        return result
