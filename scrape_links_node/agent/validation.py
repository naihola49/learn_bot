"""
Host-side validation for sandbox scrape results.
Fails closed so empty or junk rows trigger LLM repair, not false OK.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def validate_parsed_rows(
    rows: list[Any],
    max_items: int,
    *,
    min_valid_urls: int = 1,
) -> tuple[bool, str]:
    """
    Returns (True, "") if rows are usable; else (False, reason).

    A row counts as valid if it is a dict with a non-empty absolute http(s) url.
    """
    if not isinstance(rows, list):
        return False, f"Expected list, got {type(rows).__name__}"

    if len(rows) > max_items:
        return (
            False,
            f"Too many rows: got {len(rows)}, max_items={max_items}",
        )

    valid = 0
    issues: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"row[{i}] is not a dict")
            continue
        url = str(row.get("url", "") or "").strip()
        if not url:
            issues.append(f"row[{i}] missing url")
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            issues.append(f"row[{i}] invalid url: {url[:80]!r}")
            continue
        valid += 1

    if valid < min_valid_urls:
        detail = "; ".join(issues[:5])
        if len(issues) > 5:
            detail += f"; ... (+{len(issues) - 5} more)"
        return (
            False,
            f"Need at least {min_valid_urls} valid http(s) URLs, found {valid}. Issues: {detail or 'no rows'}",
        )

    return True, ""
