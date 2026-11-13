"""Read relocation lineage used only to recognize legacy, account-bound chats."""

import json
from pathlib import Path


def restoration_roots(root):
    path = Path(root) / 'data/platform/restoration.json'
    try:
        if path.stat().st_size > 64_000:
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(data, dict):
        return []
    previous = data.get('legacy_roots', [])
    if not isinstance(previous, list):
        previous = []
    values = [data.get('source_root'), *previous]
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and
                              0 < len(value) <= 4096 and not any(ord(ch) < 32 for ch in value) and
                              Path(value).is_absolute()))[:128]
