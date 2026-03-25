"""
Tier 1: article extraction with newspaper3k (download + parse).

Typical inputs: absolute article URLs from `raw/links/*.jsonl` (`url` field), e.g. NYT,
Medium, NBC News, Substack-style hosts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from newspaper import Article


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


@dataclass
class Newspaper3kResult:
    """Outcome of a single-URL newspaper3k fetch + parse."""

    url: str
    title: str = ""
    text: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str | None = None
    top_image: str | None = None
    meta_lang: str | None = None
    meta_description: str = ""
    ok: bool = False
    error: str | None = None

    def text_len(self) -> int:
        return len(self.text.strip())


def extract_with_newspaper3k(
    url: str,
    *,
    language: str = "en",
    request_timeout: int = 30,
    min_text_chars: int | None = 200,
) -> Newspaper3kResult:
    """
    Download and parse one article URL.

    `min_text_chars`: if set, `ok` is False with `error="body_too_short"` when stripped
    body is shorter (still returns partial `text` for debugging). Pass `None` to only
    require a successful download + parse.
    """
    url = (url or "").strip()
    if not url:
        return Newspaper3kResult(url=url, ok=False, error="empty_url")

    article = Article(url, language=language)
    article.config.request_timeout = request_timeout
    article.config.fetch_images = False

    try:
        article.download()
        article.parse()
    except Exception as exc:  # noqa: BLE001 — boundary for third-party network/HTML
        return Newspaper3kResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

    text = (article.text or "").strip()
    title = (article.title or "").strip()
    authors = [a for a in (article.authors or []) if isinstance(a, str) and a.strip()]
    published = _dt_to_iso(article.publish_date)
    top_image = (article.top_image or "").strip() or None
    meta_lang = (article.meta_lang or "").strip() or None
    meta_desc = (article.meta_description or "").strip()

    result = Newspaper3kResult(
        url=url,
        title=title,
        text=text,
        authors=authors,
        published_at=published,
        top_image=top_image,
        meta_lang=meta_lang,
        meta_description=meta_desc,
        ok=True,
        error=None,
    )

    if min_text_chars is not None and result.text_len() < min_text_chars:
        result.ok = False
        result.error = (
            f"body_too_short: {result.text_len()} chars (min {min_text_chars})"
        )

    return result


def row_to_article_url(row: dict[str, Any]) -> str:
    """Prefer `url` from a links JSONL object."""
    u = row.get("url")
    return u.strip() if isinstance(u, str) else ""


def _content_row_from_link_row(
    link_row: dict[str, Any],
    parsed: Newspaper3kResult,
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
        "fetch_method": "newspaper3k",
        "error": None,
    }


def build_content_rows_from_links(
    link_rows: list[dict[str, Any]],
    *,
    language: str = "en",
    request_timeout: int = 30,
    min_text_chars: int | None = 200,
) -> tuple[list[dict[str, Any]], int]:
    """
    Parse all link rows and return only successful content rows.

    Success is strictly `result.error is None` (and therefore `ok=True`).
    Returns (content_rows, failed_count).
    """
    out: list[dict[str, Any]] = []
    failed = 0
    for row in link_rows:
        url = row_to_article_url(row)
        parsed = extract_with_newspaper3k(
            url,
            language=language,
            request_timeout=request_timeout,
            min_text_chars=min_text_chars,
        )
        if parsed.error is not None:
            failed += 1
            continue
        out.append(_content_row_from_link_row(row, parsed))
    return out, failed


def write_daily_content_jsonl_from_links_file(
    links_jsonl_path: Path,
    *,
    content_dir: Path | None = None,
    language: str = "en",
    request_timeout: int = 30,
    min_text_chars: int | None = 200,
) -> tuple[Path, int, int]:
    """
    Build `raw/content/<run_date>.jsonl` from `raw/links/<run_date>.jsonl`.

    Only writes rows where newspaper3k result has `error is None`.
    Returns (output_path, written_rows, failed_rows).
    """
    if not links_jsonl_path.is_file():
        raise FileNotFoundError(f"Links JSONL not found: {links_jsonl_path}")

    run_date = links_jsonl_path.stem
    root = links_jsonl_path.parent.parent
    out_dir = content_dir or (root / "content")
    out_path = out_dir / f"{run_date}.jsonl"

    link_rows: list[dict[str, Any]] = []
    for line in links_jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            link_rows.append(item)

    content_rows, failed = build_content_rows_from_links(
        link_rows,
        language=language,
        request_timeout=request_timeout,
        min_text_chars=min_text_chars,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in content_rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    return out_path, len(content_rows), failed
