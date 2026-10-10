from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConfidenceStatus(str, Enum):
    CONFIRMED = "confirmed_by_source"
    DERIVED = "derived_from_existing_rules"
    AMBIGUOUS = "ambiguous_needs_confirmation"
    INSUFFICIENT = "insufficient_evidence"


class ApprovalStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanningStatus(str, Enum):
    READY = "ready"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"


class Operation(str, Enum):
    INSERT_BEFORE = "insert_before"
    INSERT_AFTER = "insert_after"
    REPLACE_PARAGRAPH = "replace_paragraph"
    INSERT_TABLE_ROW = "insert_table_row"
    UPDATE_TABLE_CELL = "update_table_cell"


SUPPORTED_MVP_OPERATIONS = {
    Operation.INSERT_BEFORE,
    Operation.INSERT_AFTER,
    Operation.REPLACE_PARAGRAPH,
}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str
    excerpt: str | None = None
    evidence_type: str = "source_text"


class ValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    expected: Any | None = None


class DocumentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    version: str

    @field_validator("sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        return value.lower()


class TargetLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_kind: str = "paragraph"
    heading_path: list[str] = Field(default_factory=list)
    anchor_id: str | None = None
    match_text: str | None = None
    occurrence: int | None = None

    @model_validator(mode="after")
    def require_locator(self) -> "TargetLocation":
        if not self.anchor_id and not self.match_text and not self.heading_path:
            raise ValueError("target_location requires anchor_id, match_text, or heading_path")
        return self


class ChangeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str = Field(min_length=1)
    source_document: str
    source_location: str
    target_location: TargetLocation
    operation: Operation
    proposed_content: str
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence_status: ConfidenceStatus
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    planning_status: PlanningStatus = PlanningStatus.READY
    planning_notes: list[str] = Field(default_factory=list)

    @field_validator("change_id")
    @classmethod
    def validate_change_id(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        if any(char not in allowed for char in value):
            raise ValueError("change_id may contain only letters, numbers, dash, underscore, and dot")
        return value


class ChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    plan_id: str
    created_at: datetime = Field(default_factory=utc_now)
    target_document: DocumentVersion
    changes: list[ChangeItem]
    clarification_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_change_ids(self) -> "ChangePlan":
        identifiers = [change.change_id for change in self.changes]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("change_id values must be unique within a plan")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "ChangePlan":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
