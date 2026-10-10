from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any


def load_profile(profile: str | Path | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    candidate = Path(profile)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    normalized = str(profile).lower().replace("_", "-")
    resource_name = normalized.replace("-", "_") + ".json"
    resource = resources.files("drm").joinpath("profiles", resource_name)
    if not resource.is_file():
        raise ValueError(f"Unknown business profile: {profile}")
    return json.loads(resource.read_text(encoding="utf-8"))


def analyze_profile_text(
    blocks: list[dict[str, Any]], profile: dict[str, Any] | None
) -> dict[str, Any]:
    if not profile:
        return {"profile_id": None, "indicators": [], "topics": {}}

    indicators: dict[str, list[dict[str, Any]]] = {}
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in profile.get("indicator_patterns", [])]
    topics: dict[str, list[dict[str, Any]]] = {
        topic: [] for topic in profile.get("topic_terms", {})
    }

    for block in blocks:
        if block["kind"] == "paragraph":
            texts = [(block["text"], None)]
        else:
            texts = [
                (cell["text"], {"row": cell["row"], "column": cell["column"]})
                for row in block.get("rows", [])
                for cell in row
            ]
        for text, cell_location in texts:
            for pattern in compiled:
                for match in pattern.finditer(text):
                    code = match.group(0).upper()
                    indicators.setdefault(code, []).append(
                        {
                            "anchor_id": block["anchor_id"],
                            "heading_path": block["heading_path"],
                            "cell": cell_location,
                            "excerpt": _excerpt(text, match.start(), match.end()),
                        }
                    )
            lowered = text.casefold()
            for topic, terms in profile.get("topic_terms", {}).items():
                if any(term.casefold() in lowered for term in terms):
                    topics[topic].append(
                        {
                            "anchor_id": block["anchor_id"],
                            "heading_path": block["heading_path"],
                            "cell": cell_location,
                            "excerpt": text[:240],
                        }
                    )

    return {
        "profile_id": profile.get("profile_id"),
        "indicators": [
            {"code": code, "references": references}
            for code, references in sorted(indicators.items())
        ],
        "topics": {key: value for key, value in topics.items() if value},
        "acceptance_case": profile.get("acceptance_case"),
    }


def _excerpt(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]
