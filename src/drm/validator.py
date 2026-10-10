from __future__ import annotations

import difflib
import json
import zipfile
from pathlib import Path
from typing import Any

from drm.docx_parser import parse_docx, semantic_snapshot
from drm.hashing import sha256_bytes, sha256_file
from drm.io_utils import assert_valid_docx, write_json
from drm.models import (
    ApprovalStatus,
    ChangePlan,
    Operation,
    PlanningStatus,
    SUPPORTED_MVP_OPERATIONS,
)


def validate_document(
    original_docx: str | Path,
    modified_docx: str | Path,
    plan_path: str | Path,
    output_report: str | Path,
) -> dict[str, Any]:
    original = Path(original_docx).resolve()
    modified = Path(modified_docx).resolve()
    plan = ChangePlan.load(plan_path)
    package_errors: list[str] = []
    for candidate in (original, modified):
        try:
            assert_valid_docx(candidate)
        except Exception as error:
            package_errors.append(f"{candidate}: {error}")

    original_result = parse_docx(original)
    modified_result = parse_docx(modified)
    original_snapshot = semantic_snapshot(original_result)
    actual_snapshot = semantic_snapshot(modified_result)
    expected_snapshot, expected_change_status = _expected_snapshot(original_snapshot, plan)

    expected_tokens = [_semantic_token(item) for item in expected_snapshot]
    actual_tokens = [_semantic_token(item) for item in actual_snapshot]
    semantic_matches_expected = expected_tokens == actual_tokens
    unexpected_differences = _sequence_diff(expected_tokens, actual_tokens)
    media_check = _compare_media(original_result["media"], modified_result["media"])
    target_hash_matches = sha256_file(original) == plan.target_document.sha256
    package_diff = _package_diff(original, modified)
    unexpected_package_content_parts = [
        name for name in package_diff["content_parts"] if name != "word/document.xml"
    ]

    completion = []
    for change in plan.changes:
        status = expected_change_status.get(change.change_id, "not_approved")
        completion.append(
            {
                "change_id": change.change_id,
                "approval_status": change.approval_status.value,
                "planning_status": change.planning_status.value,
                "validation_status": status if semantic_matches_expected else _observed_status(change, actual_snapshot),
            }
        )
    incomplete_approved = [
        item
        for item in completion
        if item["approval_status"] == "approved"
        and item["validation_status"] not in {"expected_applied", "observed_applied"}
    ]
    valid = all(
        [
            not package_errors,
            target_hash_matches,
            semantic_matches_expected,
            media_check["preserved"],
            not unexpected_package_content_parts,
            not incomplete_approved,
        ]
    )
    report = {
        "schema_version": "1.0",
        "status": "valid" if valid else "invalid",
        "original": {"path": str(original), "sha256": sha256_file(original)},
        "modified": {"path": str(modified), "sha256": sha256_file(modified)},
        "plan_id": plan.plan_id,
        "checks": {
            "original_hash_matches_plan": target_hash_matches,
            "docx_package_valid": not package_errors,
            "package_errors": package_errors,
            "approved_change_completion": completion,
            "semantic_content_matches_approved_plan": semantic_matches_expected,
            "unapproved_content_change_count": len(unexpected_differences),
            "unexpected_differences": unexpected_differences,
            "media_integrity": media_check,
            "style_and_location_match": semantic_matches_expected,
            "unexpected_package_content_parts": unexpected_package_content_parts,
        },
        "package_part_changes": package_diff,
        "interpretation": {
            "metadata_changes": package_diff["metadata_parts"],
            "substantive_or_structural_changes": package_diff["content_parts"],
            "note": "Package-level differences are classified separately from semantic body differences",
        },
    }
    write_json(output_report, report)
    return report


def _expected_snapshot(
    original: list[dict[str, Any]], plan: ChangePlan
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    expected = [dict(item, origin_anchor=item.get("anchor_id")) for item in original]
    status: dict[str, str] = {}
    after_tails: dict[str, int] = {}
    for change in plan.changes:
        if change.approval_status != ApprovalStatus.APPROVED:
            status[change.change_id] = "not_approved"
            continue
        if (
            change.planning_status != PlanningStatus.READY
            or change.operation not in SUPPORTED_MVP_OPERATIONS
        ):
            status[change.change_id] = "approved_but_not_executable"
            continue
        anchor = change.target_location.anchor_id
        indices = [
            index for index, item in enumerate(expected) if item.get("origin_anchor") == anchor
        ]
        if len(indices) != 1:
            status[change.change_id] = "target_not_unique"
            continue
        index = indices[0]
        target = expected[index]
        if target["kind"] != "paragraph":
            status[change.change_id] = "unsupported_target_kind"
            continue
        if change.operation == Operation.REPLACE_PARAGRAPH:
            target["text"] = change.proposed_content
            target["run_format_fingerprints"] = [
                target.get("run_format_fingerprints", ["none"])[0]
                if target.get("run_format_fingerprints")
                else "none"
            ]
        else:
            first_run_format = (
                target.get("run_format_fingerprints", ["none"])[0]
                if target.get("run_format_fingerprints")
                else "none"
            )
            inserted = {
                "kind": "paragraph",
                "anchor_id": None,
                "origin_anchor": None,
                "text": change.proposed_content,
                "style_name": target.get("style_name"),
                "paragraph_format_fingerprint": target.get("paragraph_format_fingerprint", "none"),
                "run_format_fingerprints": [first_run_format],
            }
            if change.operation == Operation.INSERT_BEFORE:
                expected.insert(index, inserted)
            else:
                tail_index = after_tails.get(anchor or "", index)
                expected.insert(tail_index + 1, inserted)
                after_tails[anchor or ""] = tail_index + 1
        status[change.change_id] = "expected_applied"
    return expected, status


def _semantic_token(item: dict[str, Any]) -> str:
    if item["kind"] == "paragraph":
        payload = {
            "kind": "paragraph",
            "text": item.get("text", ""),
            "style_name": item.get("style_name"),
            "paragraph_format_fingerprint": item.get("paragraph_format_fingerprint"),
            "run_format_fingerprints": item.get("run_format_fingerprints", []),
        }
    else:
        payload = {
            "kind": "table",
            "cells": item.get("cells", []),
            "style_name": item.get("style_name"),
            "xml_fingerprint": item.get("xml_fingerprint"),
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _sequence_diff(expected: list[str], actual: list[str]) -> list[dict[str, Any]]:
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    differences = []
    for tag, expected_start, expected_end, actual_start, actual_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        differences.append(
            {
                "kind": tag,
                "expected_range": [expected_start, expected_end],
                "actual_range": [actual_start, actual_end],
                "expected": [json.loads(value) for value in expected[expected_start:expected_end]],
                "actual": [json.loads(value) for value in actual[actual_start:actual_end]],
            }
        )
    return differences


def _observed_status(change: Any, actual: list[dict[str, Any]]) -> str:
    if change.approval_status != ApprovalStatus.APPROVED:
        return "not_approved"
    if change.planning_status != PlanningStatus.READY:
        return "approved_but_not_executable"
    matches = [
        item
        for item in actual
        if item["kind"] == "paragraph" and item.get("text") == change.proposed_content
    ]
    return "observed_applied" if matches else "not_observed"


def _compare_media(original: list[dict[str, Any]], modified: list[dict[str, Any]]) -> dict[str, Any]:
    def index(records: list[dict[str, Any]]) -> dict[str, tuple[str, int]]:
        return {
            record["part_name"]: (record["sha256"], len(record.get("referenced_by", [])))
            for record in records
        }

    original_index = index(original)
    modified_index = index(modified)
    missing = sorted(set(original_index).difference(modified_index))
    added = sorted(set(modified_index).difference(original_index))
    changed = sorted(
        name
        for name in set(original_index).intersection(modified_index)
        if original_index[name] != modified_index[name]
    )
    return {
        "preserved": not missing and not added and not changed,
        "missing_parts": missing,
        "added_parts": added,
        "changed_or_relinked_parts": changed,
    }


def _package_diff(original: Path, modified: Path) -> dict[str, list[str]]:
    with zipfile.ZipFile(original) as first, zipfile.ZipFile(modified) as second:
        first_hashes = {name: sha256_bytes(first.read(name)) for name in first.namelist()}
        second_hashes = {name: sha256_bytes(second.read(name)) for name in second.namelist()}
    names = sorted(set(first_hashes).union(second_hashes))
    changed = [name for name in names if first_hashes.get(name) != second_hashes.get(name)]
    metadata = [name for name in changed if name.startswith("docProps/")]
    content = [name for name in changed if not name.startswith("docProps/")]
    return {"all_changed_parts": changed, "metadata_parts": metadata, "content_parts": content}
