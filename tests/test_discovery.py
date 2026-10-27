import json
import sqlite3

import pytest
from pydantic import ValidationError

from portfolio_agent.cli import main
from portfolio_agent.config import DiscoveryConfig
from portfolio_agent.discovery import Discovery, Registry, marker
from portfolio_agent.github import GitHubClient, GitHubError, Issue, PublicRepository
from portfolio_agent.store import SQLiteStateStore


def repository(repo_id=1, name="owner/project", **overrides):
    return PublicRepository(
        **{
            "id": repo_id,
            "full_name": name,
            "private": False,
            "fork": False,
            "archived": False,
            "size": 0,
            "default_branch": "main",
            **overrides,
        }
    )


class FakeGitHub:
    def __init__(self):
        self.repos = [repository()]
        self.remote_issues = []
        self.posts = []
        self.fail_scan = False
        self.destination_ok = True
        self.after_create = None

    def repositories(self, account, account_type):
        if self.fail_scan:
            raise GitHubError("github_rate_limited")
        return self.repos

    def check_destination(self, destination):
        if not self.destination_ok:
            raise GitHubError("github_destination_unavailable")

    def issues(self, destination):
        return list(self.remote_issues)

    def create_issue(self, destination, title, body):
        self.posts.append((destination, title, body))
        issue = Issue(number=len(self.remote_issues) + 1, body=body)
        self.remote_issues.append(issue)
        if self.after_create:
            self.after_create()
        return issue


@pytest.fixture
def discovery(tmp_path):
    config = DiscoveryConfig(
        account="owner",
        source_repository="owner/agent",
        issue_repository="owner/approvals",
        exclude_repositories=("owner/website",),
    )
    return Discovery(Registry(tmp_path / "state.sqlite3"), FakeGitHub(), config)


def test_discovery_empty_repositories_and_explicit_filters(discovery):
    discovery.api.repos += [
        repository(2, "owner/agent"),
        repository(3, "OWNER/approvals"),
        repository(4, "owner/website"),
        repository(5, "owner/fork", fork=True),
        repository(6, "owner/archive", archived=True),
        repository(7, "owner/private", private=True),
        repository(8, "another/project"),
    ]
    plans = discovery.scan()
    assert len(plans) == 6  # Private/other-account metadata is never stored.
    assert [plan["excluded_reason"] for plan in plans] == [
        None,
        "agent_or_destination",
        "agent_or_destination",
        "configured_exclusion",
        "fork",
        "archived",
    ]
    assert discovery.registry.records()[0]["size"] == 0
    assert discovery.api.posts == []
    assert "@" not in plans[0]["body"]
    assert discovery.notify()[0]["status"] == "linked"
    assert len(discovery.api.posts) == 1


def test_forks_archived_mentions_are_explicit(discovery):
    discovery.config = discovery.config.model_copy(
        update={
            "include_forks": True,
            "include_archived": True,
            "mentions": ("reviewer",),
        }
    )
    discovery.api.repos = [repository(fork=True, archived=True)]
    plan = discovery.scan()[0]
    assert plan["excluded_reason"] is None
    assert "@reviewer" in plan["body"]


def test_restart_rename_and_closed_issue_do_not_duplicate(discovery):
    discovery.scan()
    discovery.api.remote_issues = [Issue(number=42, body=marker(1))]  # Includes closed Issues.
    discovery.notify()
    discovery.api.repos = [repository(name="owner/renamed")]
    restarted = Discovery(Registry(discovery.registry.path), discovery.api, discovery.config)
    restarted.scan()
    assert restarted.notify()[0]["issue_number"] == 42
    assert len(restarted.registry.records()) == 1
    assert restarted.registry.records()[0]["full_name"] == "owner/renamed"
    assert discovery.api.posts == []


def test_normal_repeated_notification_and_audit(discovery):
    discovery.scan()
    discovery.notify()
    discovery.notify()
    assert len(discovery.api.posts) == 1
    events = discovery.registry.events()
    assert [row["event"] for row in events[:3]] == [
        "discovery_completed",
        "issue_create_reserved",
        "issue_created",
    ]
    assert events[1]["repository_id"] == 1
    assert events[1]["destination"] == "owner/approvals"


def test_pr_or_embedded_marker_does_not_count_as_approval_issue(discovery):
    discovery.scan()
    discovery.api.remote_issues = [
        Issue(number=10, body=marker(1), pull_request={"url": "ignored"}),
        Issue(number=11, body=f"quoted {marker(1)}"),
    ]
    discovery.notify()
    assert len(discovery.api.posts) == 1


def test_accepted_post_timeout_reconciles_after_restart(discovery):
    discovery.scan()

    def timeout():
        raise GitHubError("github_request_failed")

    discovery.api.after_create = timeout
    with pytest.raises(GitHubError):
        discovery.notify()
    restarted = Discovery(Registry(discovery.registry.path), discovery.api, discovery.config)
    assert restarted.registry.records()[0]["notifications"][0]["status"] == "uncertain"
    assert restarted.notify()[0]["status"] == "linked"
    assert len(discovery.api.posts) == 1


def test_crash_before_post_or_unseen_post_never_blindly_retries(discovery):
    discovery.scan()
    with discovery.registry.transaction() as db:
        db.execute("INSERT INTO notifications VALUES(1,'owner/approvals','uncertain',NULL)")
    assert discovery.notify()[0]["status"] == "uncertain"
    assert discovery.notify()[0]["status"] == "uncertain"
    assert discovery.api.posts == []
    # An operator can manually create the Issue with the previewed body, then reconcile it.
    discovery.api.remote_issues.append(Issue(number=23, body=marker(1)))
    assert discovery.notify()[0]["issue_number"] == 23


def test_competing_notifier_observes_committed_reservation(discovery):
    discovery.scan()
    competitor = Discovery(Registry(discovery.registry.path), FakeGitHub(), discovery.config)

    def compete():
        assert competitor.notify()[0]["status"] == "uncertain"
        assert competitor.api.posts == []

    discovery.api.after_create = compete
    discovery.notify()
    assert len(discovery.api.posts) == 1


def test_failed_scan_is_atomic_and_disappeared_repos_are_ineligible(discovery):
    discovery.scan()
    original = discovery.registry.records()
    discovery.api.fail_scan = True
    with pytest.raises(GitHubError):
        discovery.scan()
    assert discovery.registry.records() == original
    discovery.api.fail_scan = False
    discovery.api.repos = []
    assert discovery.scan()[0]["excluded_reason"] == "not_in_latest_scan"
    assert discovery.notify() == []
    assert discovery.api.posts == []


def test_destination_preflight_failure_does_not_reserve(discovery):
    discovery.scan()
    discovery.api.destination_ok = False
    with pytest.raises(GitHubError):
        discovery.notify()
    assert discovery.registry.records()[0]["notifications"] == []


def test_registry_addition_preserves_phase1_and_rejects_unknown_schema(discovery):
    state = SQLiteStateStore(discovery.registry.path)
    discovery.scan()
    with state.transaction() as snapshot:
        assert snapshot.schema_version == 1
        assert snapshot.tasks == {}
    with sqlite3.connect(discovery.registry.path) as db:
        db.execute("UPDATE discovery_schema SET version=99")
    with pytest.raises(ValueError, match="unsupported registry schema"):
        Registry(discovery.registry.path)


@pytest.mark.parametrize(
    "changes",
    [
        {"account": "../../bad"},
        {"issue_repository": "owner/../bad"},
        {"issue_repository": "owner/.."},
        {"source_repository": "owner/."},
        {"mentions": ["reviewer\n@everyone"]},
        {"mentions": ["@reviewer"]},
        {"unknown": True},
    ],
)
def test_discovery_configuration_rejects_unsafe_values(discovery, changes):
    with pytest.raises(ValidationError):
        DiscoveryConfig.model_validate({**discovery.config.model_dump(), **changes})


def test_cli_preview_no_token_and_apply_requires_token(discovery, monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "state_path": str(discovery.registry.path),
                "discovery": discovery.config.model_dump(mode="json"),
            }
        )
    )
    discovery.scan()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    options = ["--config", str(config)]
    main([*options, "notify"])
    assert json.loads(capsys.readouterr().out)[0]["repository"] == "owner/project"
    with pytest.raises(SystemExit) as error:
        main([*options, "notify", "--apply"])
    assert error.value.code == 2
    assert "github_token_required" in capsys.readouterr().err
    assert discovery.registry.records()[0]["notifications"] == []
    monkeypatch.setattr(GitHubClient, "from_environment", lambda **kwargs: discovery.api)
    main([*options, "discover"])
    capsys.readouterr()
    main([*options, "notify", "--apply"])
    assert json.loads(capsys.readouterr().out)[0]["status"] == "linked"
    main([*options, "registry"])
    assert json.loads(capsys.readouterr().out)[0]["id"] == 1
    main([*options, "registry", "--audit"])
    assert json.loads(capsys.readouterr().out)[-1]["event"] == "issue_created"


def test_cli_discovery_requires_configuration(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["--state", str(tmp_path / "state.sqlite3"), "discover"])
    assert error.value.code == 2
