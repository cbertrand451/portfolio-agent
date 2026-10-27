"""Strict, non-secret JSON configuration."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def repository_path(value: str) -> str:
    if value.split("/")[-1] in {".", ".."}:
        raise ValueError("invalid repository path")
    return value


AccountName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")]
RepositoryName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$"),
    AfterValidator(repository_path),
]


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: AccountName
    account_type: Literal["user", "organization"] = "user"
    issue_repository: RepositoryName
    source_repository: RepositoryName
    exclude_repositories: tuple[RepositoryName, ...] = ()
    include_forks: bool = False
    include_archived: bool = False
    mentions: tuple[AccountName, ...] = ()


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_path: Path = Path(".portfolio-agent/state.sqlite3")
    max_retries: int = Field(default=3, ge=0, le=20)
    retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    retry_cap_seconds: int = Field(default=3600, ge=1, le=86400)
    lease_seconds: int = Field(default=300, ge=1, le=86400)
    job_budget_seconds: int = Field(default=5400, ge=1, le=21600)
    discovery: DiscoveryConfig | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "AgentConfig":
        return cls() if path is None else cls.model_validate_json(path.read_text())
