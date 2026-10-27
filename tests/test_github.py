import io
import json
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from portfolio_agent.github import GitHubClient, GitHubError, NoRedirects


class Opener:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def client(*replies):
    api = GitHubClient("secret-token")
    api._opener = Opener(replies)
    return api


def repo(repo_id=1):
    return {
        "id": repo_id,
        "full_name": f"owner/repo{repo_id}",
        "private": False,
        "fork": False,
        "archived": False,
        "size": 0,
        "default_branch": "main",
    }


@pytest.mark.parametrize("account_type,prefix", [("user", "users"), ("organization", "orgs")])
def test_repository_pagination_and_empty_metadata(account_type, prefix):
    api = client([repo(i) for i in range(1, 101)], [repo(101)])
    records = api.repositories("owner", account_type)
    assert len(records) == 101
    assert records[0].size == 0
    urls = [request.full_url for request, _ in api._opener.requests]
    repository_type = "public" if account_type == "organization" else "owner"
    assert urls == [
        f"https://api.github.com/{prefix}/owner/repos?type={repository_type}"
        "&sort=full_name&per_page=100&page=1",
        f"https://api.github.com/{prefix}/owner/repos?type={repository_type}"
        "&sort=full_name&per_page=100&page=2",
    ]
    assert all(timeout == 30 for _, timeout in api._opener.requests)


def test_issue_pagination_uses_all_states_and_post_has_fixed_destination():
    rows = [{"number": i, "body": "example"} for i in range(1, 101)]
    api = client(rows, [], {"number": 101, "body": "body"})
    assert len(api.issues("owner/approvals")) == 100
    assert "state=all" in api._opener.requests[0][0].full_url
    issue = api.create_issue("owner/approvals", "title", "body")
    request, _ = api._opener.requests[-1]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/repos/owner/approvals/issues"
    assert json.loads(request.data) == {"title": "title", "body": "body"}
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert issue.number == 101


@pytest.mark.parametrize(
    "reply,code",
    [
        (HTTPError("ignored", 429, "secret", {}, None), "github_rate_limited"),
        (HTTPError("ignored", 403, "secret", {}, None), "github_forbidden"),
        (
            HTTPError("ignored", 403, "secret", {"X-RateLimit-Remaining": "0"}, None),
            "github_rate_limited",
        ),
        (HTTPError("ignored", 401, "secret", {}, None), "github_authentication_failed"),
        (HTTPError("ignored", 404, "secret", {}, None), "github_not_found"),
        (URLError("secret"), "github_request_failed"),
        (TimeoutError("secret"), "github_request_failed"),
    ],
)
def test_transport_errors_are_safe_and_never_retried(reply, code):
    api = client(reply)
    with pytest.raises(GitHubError) as error:
        api._request("POST", "/repos/owner/project/issues", {})
    assert str(error.value) == code
    assert len(api._opener.requests) == 1


def test_invalid_json_large_payload_and_invalid_pages_fail_closed(monkeypatch):
    api = client()
    monkeypatch.setattr(api._opener, "open", lambda *args, **kwargs: io.BytesIO(b"not JSON"))
    with pytest.raises(GitHubError, match="github_request_failed"):
        api.issues("owner/project")
    monkeypatch.setattr(api._opener, "open", lambda *args, **kwargs: io.BytesIO(b"x" * 4_000_001))
    with pytest.raises(GitHubError, match="github_response_too_large"):
        api.issues("owner/project")
    with pytest.raises(GitHubError, match="github_invalid_response"):
        client({"message": "not a list"}).issues("owner/project")
    with pytest.raises(GitHubError, match="github_invalid_response"):
        client(["not an object"]).issues("owner/project")
    with pytest.raises(ValidationError):
        client([{"id": 1}]).repositories("owner", "user")


def test_page_limit_is_bounded(monkeypatch):
    api = client()
    calls = []

    def reply(method, path):
        calls.append(path)
        return [{}] * 100

    monkeypatch.setattr(api, "_request", reply)
    with pytest.raises(GitHubError, match="github_page_limit"):
        list(api._pages("/example"))
    assert len(calls) == 1000


@pytest.mark.parametrize(
    "overrides",
    [
        {"private": True},
        {"archived": True},
        {"has_issues": False},
    ],
)
def test_destination_must_be_public_unarchived_and_issue_enabled(overrides):
    api = client({**repo(), "has_issues": True, **overrides})
    with pytest.raises(GitHubError, match="github_destination_unavailable"):
        api.check_destination("owner/approvals")


def test_destination_preflight_and_missing_token(monkeypatch):
    client({**repo(), "has_issues": True}).check_destination("owner/approvals")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError, match="github_token_required"):
        GitHubClient.from_environment(require_token=True)
    with pytest.raises(GitHubError, match="github_token_required"):
        GitHubClient().create_issue("owner/project", "title", "body")
    assert NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere") is None
