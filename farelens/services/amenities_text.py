"""Normalisation, cache keys and JSON extraction for the amenities module."""

from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from typing import Any

_HTML_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</li>")
_HTML_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def normalize_amenity_for_prompt(text: str) -> str:
    out = unescape(str(text or ""))
    out = _HTML_BREAK_RE.sub(" ", out)
    out = _HTML_TAG_RE.sub(" ", out)
    out = _WS_RE.sub(" ", out)
    return out.strip()


def normalize_amenity_for_cache(text: str) -> str:
    return _WS_RE.sub("", normalize_amenity_for_prompt(text)).upper()


def build_amenity_cache_key(text: str, *, namespace: str) -> str:
    material = f"{(namespace or '').strip().lower()}\n{normalize_amenity_for_cache(text)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def extract_first_url(text: str) -> str:
    match = _URL_RE.search(str(text or ""))
    return match.group(0).rstrip(".,);]") if match else ""


def strip_urls(text: str) -> str:
    return _WS_RE.sub(" ", _URL_RE.sub("", str(text or ""))).strip(" :-")


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (tolerates prose and fences)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    fenced = _FENCE_RE.search(raw)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(raw)
    for start_char, end_char in (("{", "}"), ("[", "]")):
        a, b = raw.find(start_char), raw.rfind(end_char)
        if a != -1 and b > a:
            candidates.append(raw[a : b + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise ValueError(f"model response is not valid JSON: {last_error}")
