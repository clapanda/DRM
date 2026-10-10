from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class ResultArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    kind: str
    description: str | None = None

    @classmethod
    def local(
        cls, name: str, path: str | Path, kind: str, description: str | None = None
    ) -> "ResultArtifact":
        return cls(
            name=name,
            path=str(Path(path).resolve()),
            kind=kind,
            description=description,
        )


class ResultError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    run_id: str = Field(default_factory=lambda: f"run-{uuid4().hex}")
    operation: str
    status: RunStatus
    source_document: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ResultArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ResultError] = Field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
