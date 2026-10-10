from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from drm.docx_parser import parse_docx
from drm.hashing import sha256_file
from drm.io_utils import read_json, write_json
from drm.models import (
    ApprovalStatus,
    ChangeItem,
    ChangePlan,
    ConfidenceStatus,
    DocumentVersion,
    PlanningStatus,
    SUPPORTED_MVP_OPERATIONS,
)


def resolve_location(structure: dict[str, Any], location: Any) -> list[dict[str, Any]]:
    candidates = list(structure["blocks"])
    if location.block_kind:
        candidates = [block for block in candidates if block["kind"] == location.block_kind]
    if location.anchor_id:
        candidates = [block for block in candidates if block["anchor_id"] == location.anchor_id]
    if location.heading_path:
        wanted = list(location.heading_path)
        candidates = [block for block in candidates if block["heading_path"] == wanted]
        if not location.anchor_id and not location.match_text:
            candidates = [block for block in candidates if block.get("heading_level") is not None]
    if location.match_text:
        needle = location.match_text.casefold()
        candidates = [
            block
            for block in candidates
            if needle in _block_text(block).casefold()
        ]
    if location.occurrence is not None:
        index = location.occurrence - 1
        return [candidates[index]] if 0 <= index < len(candidates) else []
    return candidates


def build_plan(
    target_docx: str | Path,
    proposal_path: str | Path,
    output_path: str | Path,
    version: str,
    profile: str | Path | None = None,
) -> ChangePlan:
    target = Path(target_docx).resolve()
    structure = parse_docx(target, profile=profile)
    raw = read_json(proposal_path)
    raw_changes = raw if isinstance(raw, list) else raw.get("changes", [])
    if not isinstance(raw_changes, list):
        raise ValueError("Proposal must be a list or an object containing a changes list")

    changes: list[ChangeItem] = []
    clarification_questions = list(raw.get("clarification_questions", [])) if isinstance(raw, dict) else []
    for raw_change in raw_changes:
        change = ChangeItem.model_validate(raw_change)
        if change.approval_status == ApprovalStatus.APPROVED:
            change.planning_notes.append(
                "Proposal approval was reset; use the separate approve command after reviewing the anchored plan"
            )
        change.approval_status = ApprovalStatus.PENDING
        matches = resolve_location(structure, change.target_location)
        notes = list(change.planning_notes)
        if len(matches) == 1:
            match = matches[0]
            change.target_location.anchor_id = match["anchor_id"]
            change.target_location.heading_path = list(match["heading_path"])
            if change.operation not in SUPPORTED_MVP_OPERATIONS:
                change.planning_status = PlanningStatus.UNSUPPORTED
                notes.append(f"Operation {change.operation.value} is modeled but not editable in MVP")
            else:
                change.planning_status = PlanningStatus.READY
        else:
            change.planning_status = PlanningStatus.CONFLICT
            change.approval_status = ApprovalStatus.PENDING
            notes.append(
                f"Target resolved to {len(matches)} blocks; exactly one is required before approval"
            )
        if change.confidence_status in {
            ConfidenceStatus.AMBIGUOUS,
            ConfidenceStatus.INSUFFICIENT,
        }:
            clarification_questions.append(
                f"{change.change_id}: confirm the proposed business wording and its authoritative source"
            )
        if not change.evidence:
            notes.append("No evidence item is attached; approval requires human review")
        change.planning_notes = notes
        changes.append(change)

    _mark_duplicate_mutations(changes)
    plan_id = (
        raw.get("plan_id")
        if isinstance(raw, dict) and raw.get("plan_id")
        else f"plan-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    plan = ChangePlan(
        plan_id=plan_id,
        target_document=DocumentVersion(
            path=str(target), sha256=sha256_file(target), version=version
        ),
        changes=changes,
        clarification_questions=sorted(set(clarification_questions)),
        metadata={
            "profile": structure["profile_analysis"].get("profile_id"),
            "proposal_path": str(Path(proposal_path).resolve()),
            "planning_note": "Plan generation does not modify the target DOCX",
        },
    )
    write_json(output_path, plan.to_jsonable())
    return plan


def _mark_duplicate_mutations(changes: list[ChangeItem]) -> None:
    seen_replacements: dict[str, str] = {}
    for change in changes:
        anchor = change.target_location.anchor_id
        if (
            anchor
            and change.operation.value == "replace_paragraph"
            and change.planning_status == PlanningStatus.READY
        ):
            if anchor in seen_replacements:
                change.planning_status = PlanningStatus.CONFLICT
                change.approval_status = ApprovalStatus.PENDING
                change.planning_notes.append(
                    f"Target is already replaced by {seen_replacements[anchor]}"
                )
            else:
                seen_replacements[anchor] = change.change_id


def _block_text(block: dict[str, Any]) -> str:
    if block["kind"] == "paragraph":
        return block["text"]
    return "\n".join(
        cell["text"] for row in block.get("rows", []) for cell in row
    )
