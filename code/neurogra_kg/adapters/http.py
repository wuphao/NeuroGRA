"""Ollama JSON transport. Server response text is never treated as instructions."""

import json
import urllib.error
import urllib.request

from ..utils import canonical


def request_json(url: str, payload=None, timeout=30):
    data = canonical(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not include request data, patient content or credentials in errors.
        raise RuntimeError(f"ollama_http_{exc.code}") from exc


def model_info(base_url: str, model: str, timeout=10):
    models = request_json(base_url.rstrip("/") + "/api/tags", timeout=timeout).get("models", [])
    value = next((item for item in models if item.get("name") == model), None)
    if value is None or not value.get("digest"):
        raise ValueError("configured_model_not_installed:" + model)
    return value
