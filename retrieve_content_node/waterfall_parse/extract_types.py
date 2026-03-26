"""Shared types for waterfall extractors (newspaper3k, trafilatura, …)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArticleExtractResult:
    """Outcome of a single-URL article fetch + parse (any tier)."""

    url: str
    title: str = ""
    text: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str | None = None
    meta_lang: str | None = None
    meta_description: str = ""
    ok: bool = False
    error: str | None = None

    def text_len(self) -> int:
        return len(self.text.strip())


def row_to_article_url(row: dict[str, Any]) -> str:
    """Prefer `url` from a links JSONL object."""
    u = row.get("url")
    return u.strip() if isinstance(u, str) else ""


def load_link_rows_from_jsonl(links_jsonl_path: Path) -> list[dict[str, Any]]:
    """Load dict rows from a links `*.jsonl` file (skip bad lines)."""
    if not links_jsonl_path.is_file():
        raise FileNotFoundError(f"Links JSONL not found: {links_jsonl_path}")
    rows: list[dict[str, Any]] = []
    for line in links_jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def link_row_to_content_dict(
    link_row: dict[str, Any],
    parsed: ArticleExtractResult,
    fetch_method: str,
) -> dict[str, Any]:
    text = parsed.text.strip()
    return {
        "source": str(link_row.get("source") or ""),
        "title": parsed.title or str(link_row.get("title") or ""),
        "url": parsed.url,
        "guid": str(link_row.get("guid") or ""),
        "published_at": parsed.published_at or str(link_row.get("published_at") or ""),
        "content_text": text,
        "word_count": len(text.split()),
        "fetch_method": fetch_method,
        "error": None,
    }
