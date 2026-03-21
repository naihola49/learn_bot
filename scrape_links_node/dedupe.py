"""
Link row deduplication against recent JSONL history.

Primary key: non-empty `guid` (prefix g:).
Fallback: normalized `url` (prefix u:) when guid is empty.
Rows with neither guid nor url are kept (no stable key).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_KEYS = frozenset(
    k.lower()
    for k in (
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    )
)


def normalize_url_for_dedupe(url: str) -> str:
    """Stable URL string for dedupe (lowercase host, strip tracking params, trim trailing slash on path)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        if not p.scheme or not p.netloc:
            return raw.lower()
        scheme = p.scheme.lower()
        netloc = p.netloc.lower()
        path = p.path or ""
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        pairs = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_KEYS
        ]
        query = urlencode(pairs)
        return urlunparse((scheme, netloc, path, p.params, query, ""))
    except Exception:
        return raw.lower()


def dedupe_key(row: dict[str, Any]) -> str | None:
    """
    Return a stable key for this row, or None if we cannot dedupe safely.
    Prefer guid; else normalized url.
    """
    guid = str(row.get("guid") or "").strip()
    if guid:
        return f"g:{guid}"
    url = str(row.get("url") or "").strip()
    if url:
        nu = normalize_url_for_dedupe(url)
        if nu:
            return f"u:{nu}"
    return None


def _past_run_dates(run_date: str, lookback_days: int) -> list[str]:
    end = date.fromisoformat(run_date)
    return [(end - timedelta(days=i)).isoformat() for i in range(1, lookback_days + 1)]


def load_seen_keys(
    links_dir: Path,
    run_date: str,
    lookback_days: int,
) -> set[str]:
    """
    Collect dedupe keys from raw/links/<YYYY-MM-DD>.jsonl for the `lookback_days`
    calendar days **strictly before** `run_date` (not including today's file).
    """
    seen: set[str] = set()
    if lookback_days <= 0:
        return seen
    for d in _past_run_dates(run_date, lookback_days):
        path = links_dir / f"{d}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            k = dedupe_key(row)
            if k is not None:
                seen.add(k)
    return seen


def dedupe_rows(
    rows: list[dict[str, Any]],
    prior_seen: set[str],
) -> tuple[list[dict[str, Any]], int]:
    """
    Drop rows whose key already appears in `prior_seen` or earlier in this same list.
    Returns (filtered_rows, num_dropped).
    """
    local = set(prior_seen)
    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        k = dedupe_key(row)
        if k is None:
            out.append(row)
            continue
        if k in local:
            dropped += 1
            continue
        local.add(k)
        out.append(row)
    return out, dropped
