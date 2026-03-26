"""
Tier 1: article extraction with newspaper3k (download + parse).

Typical inputs: absolute article URLs from `raw/links/*.jsonl` (`url` field), e.g. NYT,
Medium, NBC News, Substack-style hosts.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from newspaper import Article

from .extract_types import (
    ArticleExtractResult,
    link_row_to_content_dict,
    load_link_rows_from_jsonl,
    row_to_article_url,
)


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def extract_with_newspaper3k(
    url: str,
    *,
    language: str = "en",
    request_timeout: int = 30,
    min_text_chars: int | None = 200,
) -> ArticleExtractResult:
    """
    Download and parse one article URL.

    `min_text_chars`: if set, `ok` is False with `error="body_too_short"` when stripped
    body is shorter (still returns partial `text` for debugging). Pass `None` to only
    require a successful download + parse.
    """
    url = (url or "").strip()
    if not url:
        return ArticleExtractResult(url=url, ok=False, error="empty_url")

    article = Article(url, language=language)
    article.config.request_timeout = request_timeout
    article.config.fetch_images = False

    try:
        article.download()
        article.parse()
    except Exception as exc:  # noqa: BLE001 — boundary for third-party network/HTML
        return ArticleExtractResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

    text = (article.text or "").strip()
    title = (article.title or "").strip()
    authors = [a for a in (article.authors or []) if isinstance(a, str) and a.strip()]
    published = _dt_to_iso(article.publish_date)
    meta_lang = (article.meta_lang or "").strip() or None
    meta_desc = (article.meta_description or "").strip()

    result = ArticleExtractResult(
        url=url,
        title=title,
        text=text,
        authors=authors,
        published_at=published,
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
        out.append(link_row_to_content_dict(row, parsed, "newspaper3k"))
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
    run_date = links_jsonl_path.stem
    root = links_jsonl_path.parent.parent
    out_dir = content_dir or (root / "content")
    out_path = out_dir / f"{run_date}.jsonl"

    link_rows = load_link_rows_from_jsonl(links_jsonl_path)

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
