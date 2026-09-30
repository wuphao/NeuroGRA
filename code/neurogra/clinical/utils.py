import hashlib
import json
import tempfile
import os
import time
from pathlib import Path
from datetime import date
from .schemas import TimeValue


def dumps(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fingerprint(value) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def identity(prefix, value) -> str:
    return prefix + "_" + fingerprint(value)[:24]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = dumps(value)
    # Separate runs may persist the same patient input concurrently.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                # Windows can briefly deny replacement while another writer or
                # scanner has the destination open. Permanent failures still fail.
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in {5, 32} or attempt == 5:
                    raise
                time.sleep(.01 * (2 ** attempt))
    finally:
        temporary.unlink(missing_ok=True)


def parse_time(raw) -> TimeValue:
    if raw is None:
        return TimeValue()
    text = str(raw)
    import re
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").rstrip("-")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
        try:
            y, m, d = map(int, normalized.split("-"))
            return TimeValue(raw=text, value=date(y, m, d).isoformat(), precision="day")
        except ValueError:
            pass
    if re.fullmatch(r"\d{4}-\d{1,2}", normalized):
        y, m = map(int, normalized.split("-"))
        if 1 <= m <= 12:
            return TimeValue(raw=text, value=f"{y:04}-{m:02}", precision="month")
    if re.fullmatch(r"\d{4}", normalized):
        return TimeValue(raw=text, value=normalized, precision="year")
    return TimeValue(raw=text, precision="text")
