from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from drm.errors import SafetyError


def write_json(path: str | Path, data: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_bytes(destination, payload.encode("utf-8"))
    return destination


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def ensure_distinct_paths(source: str | Path, output: str | Path) -> None:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    same_file = False
    if source_path.exists() and output_path.exists():
        try:
            same_file = source_path.samefile(output_path)
        except OSError:
            same_file = False
    if source_path == output_path or same_file:
        raise SafetyError("Output path must differ from the read-only source DOCX")


def ensure_output_available(path: str | Path, force: bool) -> None:
    output = Path(path)
    if output.exists() and not force:
        raise SafetyError(f"Output already exists: {output}; use --force explicitly")
    output.parent.mkdir(parents=True, exist_ok=True)


def assert_valid_docx(path: str | Path) -> None:
    document = Path(path)
    if not zipfile.is_zipfile(document):
        raise SafetyError(f"Not a valid DOCX ZIP package: {document}")
    with zipfile.ZipFile(document) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise SafetyError(f"Corrupt DOCX member: {bad_member}")
        required = {"[Content_Types].xml", "word/document.xml"}
        missing = required.difference(archive.namelist())
        if missing:
            raise SafetyError(f"DOCX is missing required parts: {sorted(missing)}")
