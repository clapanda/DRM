from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from lxml import etree

from drm.hashing import sha256_bytes, sha256_file, text_fingerprint
from drm.io_utils import assert_valid_docx, write_json
from drm.profiles import analyze_profile_text, load_profile


HEADING_PATTERN = re.compile(r"^(?:Heading|标题)\s*([1-9]\d*)$", re.IGNORECASE)
UNSUPPORTED_TAGS = {
    "fldChar": "complex_field",
    "instrText": "field_instruction",
    "txbxContent": "text_box",
    "object": "embedded_object",
    "oMath": "equation",
    "sdt": "content_control",
    "altChunk": "alternate_content_chunk",
    "ins": "tracked_insertion",
    "del": "tracked_deletion",
    "commentReference": "comment_reference",
    "bookmarkStart": "bookmark",
    "hyperlink": "hyperlink",
    "drawing": "drawing",
}


def iter_block_items(document: DocumentObject) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def paragraph_style_info(paragraph: Paragraph) -> dict[str, Any]:
    style = paragraph.style
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    return {
        "style_id": style.style_id if style is not None else None,
        "style_name": style.name if style is not None else None,
        "alignment": int(paragraph.alignment) if paragraph.alignment is not None else None,
        "has_direct_formatting": p_pr is not None and len(p_pr) > (1 if p_pr.pStyle is not None else 0),
        "numbering": {
            "num_id": str(num_pr.numId.val) if num_pr is not None and num_pr.numId is not None else None,
            "level": str(num_pr.ilvl.val) if num_pr is not None and num_pr.ilvl is not None else None,
        },
    }


def heading_level(paragraph: Paragraph) -> int | None:
    style_name = paragraph.style.name if paragraph.style is not None else ""
    match = HEADING_PATTERN.match(style_name or "")
    if match:
        return int(match.group(1))
    p_pr = paragraph._p.pPr
    if p_pr is not None:
        outline = p_pr.find(qn("w:outlineLvl"))
        if outline is not None:
            value = outline.get(qn("w:val"))
            if value is not None and value.isdigit():
                return int(value) + 1
    return None


def _table_rows(table: Table) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for row_index, tr in enumerate(table._tbl.tr_lst):
        row: list[dict[str, Any]] = []
        for column_index, tc in enumerate(tr.tc_lst):
            cell = _Cell(tc, table)
            tc_pr = tc.tcPr
            grid_span = tc_pr.gridSpan.val if tc_pr is not None and tc_pr.gridSpan is not None else 1
            vertical_merge = None
            if tc_pr is not None and tc_pr.vMerge is not None:
                vertical_merge = tc_pr.vMerge.val or "continue"
            row.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "text": cell.text,
                    "grid_span": int(grid_span),
                    "vertical_merge": str(vertical_merge) if vertical_merge is not None else None,
                    "paragraph_styles": [
                        paragraph.style.name if paragraph.style is not None else None
                        for paragraph in cell.paragraphs
                    ],
                }
            )
        rows.append(row)
    return rows


def _unsupported_counts(element: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    for node in element.iter():
        local_name = node.tag.rsplit("}", 1)[-1]
        if local_name in UNSUPPORTED_TAGS:
            counts[UNSUPPORTED_TAGS[local_name]] += 1
    return counts


def _relationship_ids(element: Any) -> list[str]:
    values: list[str] = []
    for node in element.iter():
        for attribute, value in node.attrib.items():
            if attribute in {qn("r:embed"), qn("r:id"), qn("r:link")}:
                values.append(value)
    return sorted(set(values))


def parse_docx(path: str | Path, profile: str | Path | None = None) -> dict[str, Any]:
    source = Path(path).resolve()
    assert_valid_docx(source)
    document = Document(source)
    blocks: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    unsupported = _unsupported_counts(document.element)
    relationship_references: dict[str, list[str]] = {}

    for block_index, item in enumerate(iter_block_items(document)):
        if isinstance(item, Paragraph):
            text = item.text
            level = heading_level(item)
            if level is not None and text.strip():
                heading_stack = heading_stack[: level - 1]
                while len(heading_stack) < level - 1:
                    heading_stack.append("<missing heading>")
                heading_stack.append(text.strip())
            style = paragraph_style_info(item)
            fingerprint = text_fingerprint("paragraph", style.get("style_id") or "", text)
            anchor_id = f"p-{block_index:05d}-{fingerprint[:12]}"
            block = {
                "block_index": block_index,
                "kind": "paragraph",
                "anchor_id": anchor_id,
                "fingerprint": fingerprint,
                "xml_path": f"/word/document.xml/body/*[{block_index + 1}]",
                "heading_path": list(heading_stack),
                "heading_level": level,
                "text": text,
                "style": style,
                "paragraph_format_fingerprint": _element_fingerprint(item._p.pPr),
                "run_format_fingerprints": [
                    _element_fingerprint(run._r.rPr) for run in item.runs
                ],
            }
            element = item._p
        else:
            rows = _table_rows(item)
            style_name = item.style.name if item.style is not None else None
            serialized_cells = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            fingerprint = text_fingerprint("table", style_name or "", serialized_cells)
            anchor_id = f"t-{block_index:05d}-{fingerprint[:12]}"
            block = {
                "block_index": block_index,
                "kind": "table",
                "anchor_id": anchor_id,
                "fingerprint": fingerprint,
                "xml_path": f"/word/document.xml/body/*[{block_index + 1}]",
                "heading_path": list(heading_stack),
                "style": {"style_name": style_name, "style_id": item.style.style_id if item.style else None},
                "rows": rows,
                "row_count": len(rows),
                "column_count": max((len(row) for row in rows), default=0),
                "xml_fingerprint": sha256_bytes(etree.tostring(item._tbl)),
            }
            element = item._tbl
        blocks.append(block)
        for relationship_id in _relationship_ids(element):
            relationship_references.setdefault(relationship_id, []).append(anchor_id)

    main_relationships: dict[str, list[str]] = {}
    for relationship_id, related_part in document.part.related_parts.items():
        main_relationships.setdefault(str(related_part.partname), []).append(relationship_id)
    media: list[dict[str, Any]] = []
    for related_part in document.part.package.parts:
        content_type = getattr(related_part, "content_type", "")
        part_name = str(related_part.partname)
        if not (
            (content_type.startswith("image/") and part_name.startswith("/word/media/"))
            or part_name.startswith("/word/embeddings/")
        ):
            continue
        blob = related_part.blob
        relationship_ids = sorted(main_relationships.get(part_name, []))
        referenced_by = sorted(
            {
                anchor
                for relationship_id in relationship_ids
                for anchor in relationship_references.get(relationship_id, [])
            }
        )
        media.append(
            {
                "relationship_ids": relationship_ids,
                "part_name": part_name,
                "filename": Path(part_name).name,
                "content_type": content_type,
                "size_bytes": len(blob),
                "sha256": sha256_bytes(blob),
                "referenced_by": referenced_by,
                "scope": "document_body" if relationship_ids else "package_other",
            }
        )

    selected_profile = load_profile(profile)
    profile_analysis = analyze_profile_text(blocks, selected_profile)
    report_warnings: list[str] = []
    if unsupported:
        report_warnings.append(
            "Unsupported or high-risk OOXML objects were preserved and indexed but are not editable in the MVP"
        )
    return {
        "schema_version": "1.0",
        "document": {
            "path": str(source),
            "filename": source.name,
            "sha256": sha256_file(source),
            "block_count": len(blocks),
            "paragraph_count": sum(block["kind"] == "paragraph" for block in blocks),
            "table_count": sum(block["kind"] == "table" for block in blocks),
        },
        "blocks": blocks,
        "media": media,
        "profile_analysis": profile_analysis,
        "extraction_report": {
            "status": "complete_with_limitations" if unsupported else "complete",
            "unsupported_object_counts": dict(sorted(unsupported.items())),
            "warnings": report_warnings,
            "limitations": [
                "Text inside floating text boxes is not included in normal paragraph search",
                "Field instructions and equations are preserved but not semantically interpreted",
                "Header and footer media are indexed but are not mapped to body anchors",
            ],
        },
    }


def export_inspection(
    source: str | Path, output_dir: str | Path, profile: str | Path | None = None
) -> dict[str, str]:
    result = parse_docx(source, profile=profile)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    media_directory = destination / "media"
    media_directory.mkdir(exist_ok=True)

    media_records = []
    with zipfile.ZipFile(Path(source).resolve()) as archive:
        for record in result["media"]:
            extracted_name = f"{record['sha256'][:12]}-{record['filename']}"
            extracted_path = media_directory / extracted_name
            extracted_path.write_bytes(archive.read(record["part_name"].lstrip("/")))
            copied = dict(record)
            copied["extracted_path"] = str(extracted_path.resolve())
            media_records.append(copied)

    structure = {key: value for key, value in result.items() if key not in {"media", "extraction_report"}}
    paths = {
        "document_markdown": str((destination / "document.md").resolve()),
        "structure": str((destination / "structure.json").resolve()),
        "media_index": str((destination / "media_index.json").resolve()),
        "extraction_report": str((destination / "extraction_report.json").resolve()),
    }
    Path(paths["document_markdown"]).write_text(_to_markdown(result), encoding="utf-8")
    write_json(paths["structure"], structure)
    write_json(paths["media_index"], {"schema_version": "1.0", "media": media_records})
    write_json(paths["extraction_report"], result["extraction_report"])
    return paths


def _to_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"<!-- source_sha256: {result['document']['sha256']} -->",
        "<!-- This Markdown is a read aid. The DOCX remains the editing source of truth. -->",
        "",
    ]
    for block in result["blocks"]:
        if block["kind"] == "paragraph":
            marker = f"<!-- {block['anchor_id']} -->"
            level = block.get("heading_level")
            if level is not None and block["text"].strip():
                lines.extend([marker, f"{'#' * min(level, 6)} {block['text']}", ""])
            else:
                lines.extend([marker, block["text"], ""])
        else:
            lines.extend([f"<!-- {block['anchor_id']} -->", ""])
            rows = block.get("rows", [])
            if not rows:
                lines.extend(["[Empty table]", ""])
                continue
            width = max(len(row) for row in rows)
            matrix = [
                [cell["text"].replace("|", "\\|").replace("\n", "<br>") for cell in row]
                for row in rows
            ]
            matrix = [row + [""] * (width - len(row)) for row in matrix]
            lines.append("| " + " | ".join(matrix[0]) + " |")
            lines.append("| " + " | ".join(["---"] * width) + " |")
            for row in matrix[1:]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def semantic_snapshot(result: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for block in result["blocks"]:
        if block["kind"] == "paragraph":
            snapshot.append(
                {
                    "kind": "paragraph",
                    "anchor_id": block["anchor_id"],
                    "text": block["text"],
                    "style_name": block["style"].get("style_name"),
                    "paragraph_format_fingerprint": block["paragraph_format_fingerprint"],
                    "run_format_fingerprints": block["run_format_fingerprints"],
                }
            )
        else:
            snapshot.append(
                {
                    "kind": "table",
                    "anchor_id": block["anchor_id"],
                    "cells": [[cell["text"] for cell in row] for row in block.get("rows", [])],
                    "style_name": block["style"].get("style_name"),
                    "xml_fingerprint": block["xml_fingerprint"],
                }
            )
    return snapshot


def _element_fingerprint(element: Any | None) -> str:
    if element is None:
        return "none"
    return sha256_bytes(etree.tostring(element))
