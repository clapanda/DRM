from __future__ import annotations

from pathlib import Path
from typing import Any

from drm.docx_parser import parse_docx


def search_document(
    source: str | Path,
    query: str,
    profile: str | Path | None = None,
    context_blocks: int = 1,
) -> dict[str, Any]:
    result = parse_docx(source, profile=profile)
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    blocks = result["blocks"]
    for index, block in enumerate(blocks):
        if block["kind"] == "paragraph":
            locations = [
                {"text": block["text"], "cell": None}
            ] if needle in block["text"].casefold() else []
        else:
            locations = [
                {
                    "text": cell["text"],
                    "cell": {"row": cell["row"], "column": cell["column"]},
                }
                for row in block.get("rows", [])
                for cell in row
                if needle in cell["text"].casefold()
            ]
        for location in locations:
            left = max(0, index - context_blocks)
            right = min(len(blocks), index + context_blocks + 1)
            matches.append(
                {
                    "anchor_id": block["anchor_id"],
                    "kind": block["kind"],
                    "heading_path": block["heading_path"],
                    "cell": location["cell"],
                    "text": location["text"],
                    "context": [
                        {
                            "anchor_id": context["anchor_id"],
                            "kind": context["kind"],
                            "text": context.get("text")
                            or " | ".join(
                                cell["text"]
                                for row in context.get("rows", [])
                                for cell in row
                            ),
                        }
                        for context in blocks[left:right]
                    ],
                }
            )
    return {
        "query": query,
        "source_sha256": result["document"]["sha256"],
        "match_count": len(matches),
        "matches": matches,
    }
