"""Code-owned identities. Legacy encodings remain explicit to preserve stored IDs."""

import hashlib
import json
import re
from uuid import uuid4


def _prefix(prefix):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", prefix):
        raise ValueError("invalid_identity_prefix")
    return prefix


def stable_id(prefix, value, *, encoding="compact"):
    """Use stable source/content keys, never a model-supplied identity."""
    separators = (",", ":") if encoding == "compact" else (", ", ": ")
    if encoding not in {"compact", "legacy_clinical"}:
        raise ValueError("unknown_identity_encoding")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=separators, allow_nan=False)
    return _prefix(prefix) + "_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def new_id(prefix):
    return _prefix(prefix) + "_" + uuid4().hex


def validate_references(references, allowed):
    """The caller supplies a type/patient/version-scoped allowlist."""
    unknown = set(references) - set(allowed)
    if unknown:
        raise ValueError("reference_outside_allowed_scope")
    return list(references)
