from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from drm.approval import change_approval_digest
from drm.docx_parser import iter_block_items, parse_docx
from drm.errors import PlanConflictError, SafetyError, UnsupportedEditError
from drm.hashing import sha256_file
from drm.io_utils import (
    assert_valid_docx,
    ensure_distinct_paths,
    ensure_output_available,
    write_json,
)
from drm.models import (
    ApprovalStatus,
    ChangePlan,
    Operation,
    PlanningStatus,
    SUPPORTED_MVP_OPERATIONS,
)


HIGH_RISK_REPLACEMENT_TAGS = {
    "drawing",
    "object",
    "fldChar",
    "instrText",
    "bookmarkStart",
    "bookmarkEnd",
    "hyperlink",
    "sdt",
    "txbxContent",
    "pict",
    "oMath",
}


def apply_plan(
    source_docx: str | Path,
    plan_path: str | Path,
    output_docx: str | Path,
    audit_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    source = Path(source_docx).resolve()
    output = Path(output_docx).resolve()
    plan_file = Path(plan_path).resolve()
    ensure_distinct_paths(source, output)
    ensure_output_available(output, force=force)
    assert_valid_docx(source)
    plan = ChangePlan.load(plan_file)
    _validate_approval_history(plan)
    current_hash = sha256_file(source)
    if current_hash != plan.target_document.sha256:
        raise SafetyError(
            "Source hash does not match the approved plan target; create a new plan for this version"
        )

    structure = parse_docx(source)
    document = Document(source)
    paragraph_by_anchor = _paragraph_map(document, structure)
    audit: dict[str, Any] = {
        "schema_version": "1.0",
        "plan_id": plan.plan_id,
        "plan_sha256": sha256_file(plan_file),
        "source_path": str(source),
        "source_sha256_before": current_hash,
        "output_path": str(output),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "applied_changes": [],
        "skipped_changes": [],
        "status": "running",
    }
    insertion_tails: dict[str, Any] = {}

    for change in plan.changes:
        if change.approval_status != ApprovalStatus.APPROVED:
            audit["skipped_changes"].append(
                {"change_id": change.change_id, "reason": "not_approved"}
            )
            continue
        if change.planning_status != PlanningStatus.READY:
            audit["skipped_changes"].append(
                {
                    "change_id": change.change_id,
                    "reason": f"planning_status_{change.planning_status.value}",
                }
            )
            continue
        if change.operation not in SUPPORTED_MVP_OPERATIONS:
            audit["skipped_changes"].append(
                {"change_id": change.change_id, "reason": "unsupported_operation"}
            )
            continue
        anchor = change.target_location.anchor_id
        target = paragraph_by_anchor.get(anchor or "")
        if target is None:
            raise PlanConflictError(
                f"Approved change {change.change_id} no longer resolves to its paragraph anchor"
            )
        try:
            if change.operation == Operation.REPLACE_PARAGRAPH:
                _replace_paragraph_text(target, change.proposed_content)
            elif change.operation == Operation.INSERT_BEFORE:
                _insert_paragraph(target._p, change.proposed_content, before=True)
            elif change.operation == Operation.INSERT_AFTER:
                base = insertion_tails.get(anchor or "", target._p)
                inserted = _insert_paragraph(base, change.proposed_content, before=False)
                insertion_tails[anchor or ""] = inserted
            audit["applied_changes"].append(
                {
                    "change_id": change.change_id,
                    "operation": change.operation.value,
                    "anchor_id": anchor,
                }
            )
        except UnsupportedEditError as error:
            audit["skipped_changes"].append(
                {"change_id": change.change_id, "reason": str(error)}
            )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".docx.tmp", dir=output.parent
    )
    os.close(fd)
    try:
        document.save(temporary_name)
        assert_valid_docx(temporary_name)
        if sha256_file(source) != current_hash:
            raise SafetyError("Source DOCX changed during apply; output was not published")
        os.replace(temporary_name, output)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    audit["source_sha256_after"] = sha256_file(source)
    audit["output_sha256"] = sha256_file(output)
    audit["completed_at"] = datetime.now(timezone.utc).isoformat()
    audit["status"] = "partial" if audit["skipped_changes"] else "complete"
    destination_audit = Path(audit_path) if audit_path else output.with_suffix(".audit.json")
    write_json(destination_audit, audit)
    audit["audit_path"] = str(destination_audit.resolve())
    return audit


def _validate_approval_history(plan: ChangePlan) -> None:
    approved = {
        change.change_id: change_approval_digest(change)
        for change in plan.changes
        if change.approval_status == ApprovalStatus.APPROVED
    }
    if not approved:
        return
    recorded: dict[str, set[str]] = {}
    for entry in plan.metadata.get("approval_history", []):
        if not entry.get("approver") or not entry.get("approved_at"):
            continue
        for item in entry.get("approved_changes", []):
            change_id = item.get("change_id")
            digest = item.get("sha256")
            if change_id and digest:
                recorded.setdefault(change_id, set()).add(digest)
    missing_or_changed = sorted(
        change_id
        for change_id, digest in approved.items()
        if digest not in recorded.get(change_id, set())
    )
    if missing_or_changed:
        raise SafetyError(
            "Approved changes lack matching DRM approval records or were modified after approval: "
            + ", ".join(missing_or_changed)
        )


def _paragraph_map(document: Any, structure: dict[str, Any]) -> dict[str, Paragraph]:
    items = list(iter_block_items(document))
    if len(items) != len(structure["blocks"]):
        raise SafetyError("Document structure changed while preparing the edit")
    mapping: dict[str, Paragraph] = {}
    for item, block in zip(items, structure["blocks"], strict=True):
        if block["kind"] == "paragraph" and isinstance(item, Paragraph):
            mapping[block["anchor_id"]] = item
    return mapping


def _replace_paragraph_text(paragraph: Paragraph, content: str) -> None:
    risky = sorted(
        {
            node.tag.rsplit("}", 1)[-1]
            for node in paragraph._p.iter()
            if node.tag.rsplit("}", 1)[-1] in HIGH_RISK_REPLACEMENT_TAGS
        }
    )
    if risky:
        raise UnsupportedEditError(
            "unsafe_replacement_target_contains_" + "_".join(risky)
        )
    first_run_properties = None
    if paragraph.runs and paragraph.runs[0]._r.rPr is not None:
        first_run_properties = copy.deepcopy(paragraph.runs[0]._r.rPr)
    p_pr = paragraph._p.pPr
    for child in list(paragraph._p):
        if child is not p_pr:
            paragraph._p.remove(child)
    run = OxmlElement("w:r")
    if first_run_properties is not None:
        run.append(first_run_properties)
    run.append(_text_element(content))
    paragraph._p.append(run)


def _insert_paragraph(reference: Any, content: str, before: bool) -> Any:
    paragraph = OxmlElement("w:p")
    reference_p_pr = reference.find(qn("w:pPr"))
    if reference_p_pr is not None:
        paragraph.append(copy.deepcopy(reference_p_pr))
    run = OxmlElement("w:r")
    first_reference_run = reference.find(qn("w:r"))
    if first_reference_run is not None:
        r_pr = first_reference_run.find(qn("w:rPr"))
        if r_pr is not None:
            run.append(copy.deepcopy(r_pr))
    run.append(_text_element(content))
    paragraph.append(run)
    if before:
        reference.addprevious(paragraph)
    else:
        reference.addnext(paragraph)
    return paragraph


def _text_element(content: str) -> Any:
    text = OxmlElement("w:t")
    if content[:1].isspace() or content[-1:].isspace():
        text.set(qn("xml:space"), "preserve")
    text.text = content
    return text
