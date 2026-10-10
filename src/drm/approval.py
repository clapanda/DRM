from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from drm.errors import PlanConflictError
from drm.io_utils import write_json
from drm.models import (
    ApprovalStatus,
    ChangePlan,
    ChangeItem,
    ConfidenceStatus,
    PlanningStatus,
)


def change_approval_digest(change: ChangeItem) -> str:
    payload = change.model_dump(mode="json")
    payload.pop("approval_status", None)
    payload.pop("planning_notes", None)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def approve_changes(
    plan_path: str | Path,
    output_path: str | Path,
    change_ids: list[str],
    approver: str,
    allow_uncertain: bool = False,
) -> ChangePlan:
    plan = ChangePlan.load(plan_path)
    requested = set(change_ids)
    known = {change.change_id for change in plan.changes}
    unknown = sorted(requested.difference(known))
    if unknown:
        raise PlanConflictError(f"Unknown change IDs: {unknown}")
    for change in plan.changes:
        if change.change_id not in requested:
            continue
        if change.planning_status != PlanningStatus.READY:
            raise PlanConflictError(
                f"Cannot approve {change.change_id}: planning status is {change.planning_status.value}"
            )
        if (
            change.confidence_status
            in {ConfidenceStatus.AMBIGUOUS, ConfidenceStatus.INSUFFICIENT}
            and not allow_uncertain
        ):
            raise PlanConflictError(
                f"Cannot approve uncertain change {change.change_id} without --allow-uncertain"
            )
        change.approval_status = ApprovalStatus.APPROVED
    history = list(plan.metadata.get("approval_history", []))
    history.append(
        {
            "approver": approver,
            "approved_change_ids": sorted(requested),
            "approved_changes": [
                {
                    "change_id": change.change_id,
                    "sha256": change_approval_digest(change),
                }
                for change in plan.changes
                if change.change_id in requested
            ],
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "allow_uncertain": allow_uncertain,
        }
    )
    plan.metadata["approval_history"] = history
    write_json(output_path, plan.to_jsonable())
    return plan
