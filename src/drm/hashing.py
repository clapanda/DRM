from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def text_fingerprint(*parts: str) -> str:
    normalized = "\u241f".join(" ".join(part.split()) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
