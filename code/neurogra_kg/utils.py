"""Stable encoding, identity and atomic file writes."""

import hashlib
import json
import os
import tempfile
import re
from pathlib import Path
from neurogra_common.identity import stable_id


def safe_identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or ".." in value:
        raise ValueError("invalid_run_identifier")
    return value


def mentions_term(text, term):
    """Match Chinese names and bounded Latin aliases without matching AD in lead."""
    term = term.strip()
    if not term:
        return False
    left = r"(?<![A-Za-z0-9_])" if term[0].isascii() and (term[0].isalnum() or term[0] == "_") else ""
    right = r"(?![A-Za-z0-9_])" if term[-1].isascii() and (term[-1].isalnum() or term[-1] == "_") else ""
    return re.search(left + re.escape(term) + right, text, re.IGNORECASE) is not None


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(prefix: str, value) -> str:
    return stable_id(prefix, value)


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".kg-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
