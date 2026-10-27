"""Bounded GitHub REST adapter. No repository code is fetched or executed."""

import json
import os
from collections.abc import Iterator
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field

from .config import AccountName, RepositoryName


class GitHubError(Exception):
    """Fixed, safe codes only; never include response bodies, headers, or tokens."""


class PublicRepository(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int = Field(gt=0, strict=True)
    full_name: RepositoryName
    private: bool = Field(strict=True)
    fork: bool = Field(strict=True)
    archived: bool = Field(strict=True)
    size: int = Field(ge=0, strict=True)
    default_branch: str | None = None


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    number: int = Field(gt=0, strict=True)
    body: str | None = None
    pull_request: dict[str, Any] | None = None


class NoRedirects(HTTPRedirectHandler):
    # Do not forward credentials or repeat a POST to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, token: str | None = None):
        self._token = token
        self._opener = build_opener(NoRedirects())

    @classmethod
    def from_environment(cls, *, require_token: bool = False) -> "GitHubClient":
        token = os.environ.get("GITHUB_TOKEN") or None
        if require_token and not token:
            raise GitHubError("github_token_required")
        return cls(token)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-portfolio-agent",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(f"https://api.github.com{path}", data, headers, method=method)
        try:
            with self._opener.open(request, timeout=30) as response:
                payload = response.read(4_000_001)
                if len(payload) > 4_000_000:
                    raise GitHubError("github_response_too_large")
                return json.loads(payload)
        except HTTPError as error:
            limited = error.code == 429 or (
                error.code == 403
                and (
                    error.headers.get("X-RateLimit-Remaining") == "0"
                    or error.headers.get("Retry-After") is not None
                )
            )
            code = (
                "github_rate_limited"
                if limited
                else {
                    401: "github_authentication_failed",
                    403: "github_forbidden",
                    404: "github_not_found",
                }.get(error.code, "github_http_error")
            )
            raise GitHubError(code) from None
        except (URLError, OSError, ValueError, HTTPException):
            raise GitHubError("github_request_failed") from None

    def _pages(self, path: str) -> Iterator[dict]:
        # Numeric pagination stays on a fixed host and endpoint; never follow Link URLs.
        separator = "&" if "?" in path else "?"
        for page in range(1, 1001):
            records = self._request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
                raise GitHubError("github_invalid_response")
            yield from records
            if len(records) < 100:
                return
        raise GitHubError("github_page_limit")

    def repositories(self, account: AccountName, account_type: str) -> list[PublicRepository]:
        prefix = "orgs" if account_type == "organization" else "users"
        # The user endpoint accepts owner/all/member; only the org endpoint accepts public.
        repository_type = "public" if account_type == "organization" else "owner"
        records = self._pages(f"/{prefix}/{account}/repos?type={repository_type}&sort=full_name")
        return [PublicRepository.model_validate(record) for record in records]

    def issues(self, repository: RepositoryName) -> list[Issue]:
        records = self._pages(f"/repos/{repository}/issues?state=all&sort=created&direction=asc")
        return [Issue.model_validate(record) for record in records]

    def check_destination(self, repository: RepositoryName) -> None:
        record = self._request("GET", f"/repos/{repository}")
        metadata = PublicRepository.model_validate(record)
        if metadata.private or metadata.archived or record.get("has_issues") is not True:
            raise GitHubError("github_destination_unavailable")

    def create_issue(self, repository: RepositoryName, title: str, body: str) -> Issue:
        if not self._token:
            raise GitHubError("github_token_required")
        record = self._request(
            "POST", f"/repos/{repository}/issues", {"title": title, "body": body}
        )
        return Issue.model_validate(record)
