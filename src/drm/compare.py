from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from drm.docx_parser import parse_docx, semantic_snapshot
from drm.io_utils import write_json


def compare_documents(
    left_docx: str | Path,
    right_docx: str | Path,
    output_path: str | Path,
    profile: str | Path | None = None,
) -> dict[str, Any]:
    left = parse_docx(left_docx, profile=profile)
    right = parse_docx(right_docx, profile=profile)
    left_snapshot = semantic_snapshot(left)
    right_snapshot = semantic_snapshot(right)
    left_tokens = [_comparison_token(item) for item in left_snapshot]
    right_tokens = [_comparison_token(item) for item in right_snapshot]
    matcher = difflib.SequenceMatcher(a=left_tokens, b=right_tokens, autojunk=False)
    differences: list[dict[str, Any]] = []
    counts = {"equal": 0, "insert": 0, "delete": 0, "replace": 0}
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        counts[tag] += max(left_end - left_start, right_end - right_start)
        if tag == "equal":
            continue
        differences.append(
            {
                "kind": tag,
                "left_range": [left_start, left_end],
                "right_range": [right_start, right_end],
                "left_blocks": [
                    _compact_block(block) for block in left_snapshot[left_start:left_end]
                ],
                "right_blocks": [
                    _compact_block(block) for block in right_snapshot[right_start:right_end]
                ],
            }
        )

    indicator_comparison = _compare_indicators(
        left["profile_analysis"].get("indicators", []),
        right["profile_analysis"].get("indicators", []),
    )
    warnings = sorted(
        set(left["extraction_report"].get("warnings", []))
        | set(right["extraction_report"].get("warnings", []))
    )
    report = {
        "schema_version": "1.0",
        "left_document": left["document"],
        "right_document": right["document"],
        "summary": {
            "documents_identical_by_hash": left["document"]["sha256"]
            == right["document"]["sha256"],
            "semantic_change_group_count": len(differences),
            "opcode_block_counts": counts,
        },
        "indicator_comparison": indicator_comparison,
        "differences": differences,
        "warnings": warnings,
        "limitations": [
            "Comparison is deterministic and structural; it does not decide whether a business rule is correct",
            "High-risk OOXML objects are reported by inspection and preserved but not semantically compared in full",
        ],
    }
    write_json(output_path, report)
    return report


def _comparison_token(item: dict[str, Any]) -> str:
    payload = dict(item)
    payload.pop("anchor_id", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _compact_block(item: dict[str, Any]) -> dict[str, Any]:
    if item["kind"] == "paragraph":
        return {
            "kind": "paragraph",
            "text": item.get("text", "")[:500],
            "style_name": item.get("style_name"),
        }
    return {
        "kind": "table",
        "row_count": len(item.get("cells", [])),
        "preview": [row[:4] for row in item.get("cells", [])[:4]],
        "style_name": item.get("style_name"),
    }


def _compare_indicators(
    left_records: list[dict[str, Any]], right_records: list[dict[str, Any]]
) -> dict[str, Any]:
    left = {record["code"]: len(record.get("references", [])) for record in left_records}
    right = {record["code"]: len(record.get("references", [])) for record in right_records}
    left_codes = set(left)
    right_codes = set(right)
    return {
        "added_codes": sorted(right_codes - left_codes),
        "removed_codes": sorted(left_codes - right_codes),
        "shared_codes": sorted(left_codes & right_codes),
        "reference_count_changes": [
            {"code": code, "left": left[code], "right": right[code]}
            for code in sorted(left_codes & right_codes)
            if left[code] != right[code]
        ],
    }
