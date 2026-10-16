"""Strict, non-secret JSON configuration."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_path: Path = Path(".portfolio-agent/state.sqlite3")
    max_retries: int = Field(default=3, ge=0, le=20)
    retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    retry_cap_seconds: int = Field(default=3600, ge=1, le=86400)
    lease_seconds: int = Field(default=300, ge=1, le=86400)
    job_budget_seconds: int = Field(default=5400, ge=1, le=21600)

    @classmethod
    def load(cls, path: Path | None = None) -> "AgentConfig":
        return cls() if path is None else cls.model_validate_json(path.read_text())
