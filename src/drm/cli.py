from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from drm import __version__
from drm.approval import approve_changes
from drm.compare import compare_documents
from drm.docx_parser import export_inspection
from drm.editor import apply_plan
from drm.errors import DRMError
from drm.hashing import sha256_file
from drm.io_utils import read_json, write_json
from drm.models import ChangePlan
from drm.planner import build_plan
from drm.result_protocol import (
    OperationResult,
    ResultArtifact,
    ResultError,
    RunStatus,
)
from drm.search import search_document
from drm.validator import validate_document


LOGGER = logging.getLogger("drm")


def _add_result_file(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--result-file",
        help="Write a compact, uniform machine-readable operation result",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drm", description="Approval-gated DOCX requirement maintenance"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Extract a read-only structural view")
    inspect_parser.add_argument("source")
    inspect_parser.add_argument("--output-dir", required=True)
    inspect_parser.add_argument("--profile")
    _add_result_file(inspect_parser)

    search_parser = subparsers.add_parser("search", help="Search paragraphs and table cells")
    search_parser.add_argument("source")
    search_parser.add_argument("query")
    search_parser.add_argument("--profile")
    search_parser.add_argument("--context-blocks", type=int, default=1)
    search_parser.add_argument("--output")
    _add_result_file(search_parser)

    compare_parser = subparsers.add_parser(
        "compare", help="Compare two DOCX files structurally without modifying them"
    )
    compare_parser.add_argument("left")
    compare_parser.add_argument("right")
    compare_parser.add_argument("--output", required=True)
    compare_parser.add_argument("--profile")
    _add_result_file(compare_parser)

    plan_parser = subparsers.add_parser("plan", help="Validate and anchor a proposed change plan")
    plan_parser.add_argument("target")
    plan_parser.add_argument("--proposal", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--document-version", required=True)
    plan_parser.add_argument("--profile")
    _add_result_file(plan_parser)

    approve_parser = subparsers.add_parser(
        "approve", help="Explicitly approve selected conflict-free plan items"
    )
    approve_parser.add_argument("plan")
    approve_parser.add_argument("--change-id", action="append", required=True)
    approve_parser.add_argument("--approver", required=True)
    approve_parser.add_argument("--output", required=True)
    approve_parser.add_argument("--allow-uncertain", action="store_true")
    _add_result_file(approve_parser)

    schema_parser = subparsers.add_parser("schema", help="Export the change-plan JSON Schema")
    schema_parser.add_argument("--output", required=True)
    _add_result_file(schema_parser)

    result_schema_parser = subparsers.add_parser(
        "result-schema", help="Export the uniform operation-result JSON Schema"
    )
    result_schema_parser.add_argument("--output", required=True)
    _add_result_file(result_schema_parser)

    apply_parser = subparsers.add_parser("apply", help="Apply approved safe changes to a copy")
    apply_parser.add_argument("source")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--audit-log")
    apply_parser.add_argument("--force", action="store_true")
    _add_result_file(apply_parser)

    validate_parser = subparsers.add_parser("validate", help="Validate output against an approved plan")
    validate_parser.add_argument("original")
    validate_parser.add_argument("modified")
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--output", required=True)
    _add_result_file(validate_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    result_file = getattr(args, "result_file", None)
    try:
        result = _execute(args)
        if result_file:
            envelope = _operation_result(args, result)
            write_json(result_file, envelope.to_jsonable())
            printable = envelope.to_jsonable()
        else:
            printable = result
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
        if args.command == "validate" and result["status"] != "valid":
            return 3
        return 0
    except (DRMError, ValueError, OSError) as error:
        if result_file:
            envelope = OperationResult(
                operation=args.command,
                status=RunStatus.FAILED,
                source_document=_source_on_error(args),
                errors=[ResultError(code=error.__class__.__name__, message=str(error))],
            )
            write_json(result_file, envelope.to_jsonable())
            print(json.dumps(envelope.to_jsonable(), ensure_ascii=False, indent=2))
        else:
            LOGGER.error("%s", error)
        return 2


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "inspect":
        return export_inspection(args.source, args.output_dir, profile=args.profile)
    if args.command == "search":
        result = search_document(
            args.source,
            args.query,
            profile=args.profile,
            context_blocks=args.context_blocks,
        )
        output = args.output
        if args.result_file and not output:
            result_path = Path(args.result_file)
            output = result_path.with_name(result_path.stem + ".details.json")
            args.output = str(output)
        if output:
            write_json(output, result)
        return result
    if args.command == "compare":
        return compare_documents(args.left, args.right, args.output, profile=args.profile)
    if args.command == "plan":
        plan = build_plan(
            args.target,
            args.proposal,
            args.output,
            version=args.document_version,
            profile=args.profile,
        )
        return plan.to_jsonable()
    if args.command == "approve":
        plan = approve_changes(
            args.plan,
            args.output,
            args.change_id,
            approver=args.approver,
            allow_uncertain=args.allow_uncertain,
        )
        return plan.to_jsonable()
    if args.command == "schema":
        result = ChangePlan.model_json_schema()
        write_json(args.output, result)
        return result
    if args.command == "result-schema":
        result = OperationResult.model_json_schema()
        write_json(args.output, result)
        return result
    if args.command == "apply":
        return apply_plan(
            args.source,
            args.plan,
            args.output,
            audit_path=args.audit_log,
            force=args.force,
        )
    return validate_document(args.original, args.modified, args.plan, args.output)


def _operation_result(args: argparse.Namespace, result: dict[str, Any]) -> OperationResult:
    command = args.command
    if command == "inspect":
        structure = read_json(result["structure"])
        extraction = read_json(result["extraction_report"])
        analysis = structure.get("profile_analysis", {})
        findings = [
            {
                "type": "document_summary",
                "block_count": structure["document"]["block_count"],
                "paragraph_count": structure["document"]["paragraph_count"],
                "table_count": structure["document"]["table_count"],
                "media_count": len(read_json(result["media_index"])["media"]),
            },
            {
                "type": "profile_summary",
                "profile_id": analysis.get("profile_id"),
                "indicator_codes": [item["code"] for item in analysis.get("indicators", [])],
                "topic_counts": {
                    key: len(value) for key, value in analysis.get("topics", {}).items()
                },
            },
        ]
        artifacts = [
            ResultArtifact.local(name, path, "inspection_output")
            for name, path in result.items()
        ]
        return OperationResult(
            operation=command,
            status=RunStatus.SUCCESS,
            source_document=structure["document"],
            findings=findings,
            artifacts=artifacts,
            warnings=extraction.get("warnings", []),
        )

    if command == "search":
        matches = result.get("matches", [])
        warnings = []
        if len(matches) > 20:
            warnings.append("Match anchors were truncated in the summary; read the detailed artifact")
        artifacts = []
        if args.output:
            artifacts.append(
                ResultArtifact.local(
                    "search_details", args.output, "search_report", "Full matches and context"
                )
            )
        return OperationResult(
            operation=command,
            status=RunStatus.SUCCESS,
            source_document={
                "path": str(Path(args.source).resolve()),
                "sha256": result["source_sha256"],
            },
            findings=[
                {
                    "type": "search_summary",
                    "query": result["query"],
                    "match_count": result["match_count"],
                    "anchors": [match["anchor_id"] for match in matches[:20]],
                }
            ],
            artifacts=artifacts,
            warnings=warnings,
        )

    if command == "compare":
        return OperationResult(
            operation=command,
            status=RunStatus.SUCCESS,
            source_document={"left": result["left_document"], "right": result["right_document"]},
            findings=[
                {"type": "comparison_summary", **result["summary"]},
                {"type": "indicator_comparison", **result["indicator_comparison"]},
            ],
            artifacts=[ResultArtifact.local("comparison_report", args.output, "comparison_report")],
            warnings=result.get("warnings", []) + result.get("limitations", []),
        )

    if command == "plan":
        counts = Counter(change["planning_status"] for change in result["changes"])
        ready_count = counts.get("ready", 0)
        status = RunStatus.SUCCESS
        if counts.get("conflict", 0) or counts.get("unsupported", 0):
            status = RunStatus.PARTIAL if ready_count else RunStatus.BLOCKED
        warnings = [
            note
            for change in result["changes"]
            for note in change.get("planning_notes", [])
        ]
        return OperationResult(
            operation=command,
            status=status,
            source_document=result["target_document"],
            findings=[
                {
                    "type": "plan_summary",
                    "plan_id": result["plan_id"],
                    "change_count": len(result["changes"]),
                    "planning_status_counts": dict(counts),
                    "clarification_question_count": len(result["clarification_questions"]),
                }
            ],
            artifacts=[ResultArtifact.local("change_plan", args.output, "change_plan")],
            warnings=warnings,
        )

    if command == "approve":
        approved = [
            change["change_id"]
            for change in result["changes"]
            if change["approval_status"] == "approved"
        ]
        return OperationResult(
            operation=command,
            status=RunStatus.SUCCESS,
            source_document=result["target_document"],
            findings=[
                {
                    "type": "approval_summary",
                    "plan_id": result["plan_id"],
                    "approved_change_ids": approved,
                }
            ],
            artifacts=[ResultArtifact.local("approved_change_plan", args.output, "change_plan")],
        )

    if command in {"schema", "result-schema"}:
        return OperationResult(
            operation=command,
            status=RunStatus.SUCCESS,
            findings=[{"type": "schema_summary", "title": result.get("title")}],
            artifacts=[ResultArtifact.local("json_schema", args.output, "json_schema")],
        )

    if command == "apply":
        skipped = result.get("skipped_changes", [])
        return OperationResult(
            operation=command,
            status=RunStatus.PARTIAL if skipped else RunStatus.SUCCESS,
            source_document={
                "path": result["source_path"],
                "sha256": result["source_sha256_before"],
            },
            findings=[
                {
                    "type": "apply_summary",
                    "plan_id": result["plan_id"],
                    "applied_change_ids": [
                        change["change_id"] for change in result["applied_changes"]
                    ],
                    "skipped_changes": skipped,
                    "output_sha256": result["output_sha256"],
                }
            ],
            artifacts=[
                ResultArtifact.local("modified_document", args.output, "docx"),
                ResultArtifact.local("audit_log", result["audit_path"], "audit_log"),
            ],
            warnings=[f"{item['change_id']}: {item['reason']}" for item in skipped],
        )

    valid = result["status"] == "valid"
    checks = result["checks"]
    return OperationResult(
        operation=command,
        status=RunStatus.SUCCESS if valid else RunStatus.FAILED,
        source_document={"original": result["original"], "modified": result["modified"]},
        findings=[
            {
                "type": "validation_summary",
                "validation_status": result["status"],
                "unapproved_content_change_count": checks["unapproved_content_change_count"],
                "media_preserved": checks["media_integrity"]["preserved"],
                "unexpected_package_content_parts": checks[
                    "unexpected_package_content_parts"
                ],
            }
        ],
        artifacts=[ResultArtifact.local("validation_report", args.output, "validation_report")],
        warnings=[] if valid else ["Validation failed; inspect the full report before use"],
        errors=[]
        if valid
        else [ResultError(code="validation_failed", message="Modified DOCX did not pass validation")],
    )


def _source_on_error(args: argparse.Namespace) -> dict[str, Any] | None:
    for attribute in ("source", "target", "original", "left", "plan"):
        value = getattr(args, attribute, None)
        if value:
            path = Path(value).resolve()
            source: dict[str, Any] = {"path": str(path)}
            if path.is_file() and path.suffix.lower() in {".docx", ".docm"}:
                try:
                    source["sha256"] = sha256_file(path)
                except OSError:
                    pass
            return source
    return None


if __name__ == "__main__":
    raise SystemExit(main())
