"""
Tier 2: article extraction with trafilatura (fetch HTML + main text + metadata).

Same result shape as newspaper3k via shared `ArticleExtractResult`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import trafilatura

from .extract_types import (
    ArticleExtractResult,
    link_row_to_content_dict,
    load_link_rows_from_jsonl,
    row_to_article_url,
)


def _authors_from_trafilatura(author: Any) -> list[str]:
    if author is None:
        return []
    if isinstance(author, list):
        return [str(a).strip() for a in author if str(a).strip()]
    s = str(author).strip()
    if not s:
        return []
    for sep in (";", ","):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


def _published_from_doc(doc: Any) -> str | None:
    raw = getattr(doc, "date", None)
    if raw is None:
        return None
    if hasattr(raw, "isoformat"):
        try:
            return raw.isoformat()
        except (TypeError, ValueError):
            pass
    s = str(raw).strip()
    return s or None


def extract_with_trafilatura(
    url: str,
    *,
    favor_recall: bool = True,
    min_text_chars: int | None = 200,
) -> ArticleExtractResult:
    """
    Download HTML and extract main text + metadata.

    `min_text_chars`: same semantics as newspaper3k — if set and body is shorter,
    sets `ok=False` and `error="body_too_short: …"` while still returning `text`.
    """
    url = (url or "").strip()
    if not url:
        return ArticleExtractResult(url=url, ok=False, error="empty_url")

    try:
        html = trafilatura.fetch_url(url)
    except Exception as exc:  # noqa: BLE001
        return ArticleExtractResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

    if not html:
        return ArticleExtractResult(url=url, ok=False, error="fetch_failed")

    try:
        try:
            doc = trafilatura.bare_extraction(
                html,
                url=url,
                with_metadata=True,
                favor_recall=favor_recall,
                include_comments=False,
            )
        except TypeError:
            doc = trafilatura.bare_extraction(
                html,
                url=url,
                with_metadata=True,
                include_comments=False,
            )
    except Exception as exc:  # noqa: BLE001
        return ArticleExtractResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

    if doc is None:
        return ArticleExtractResult(url=url, ok=False, error="bare_extraction_empty")

    text = (getattr(doc, "text", None) or "").strip()
    title = (getattr(doc, "title", None) or "").strip()
    authors = _authors_from_trafilatura(getattr(doc, "author", None))
    published = _published_from_doc(doc)
    meta_lang = (getattr(doc, "language", None) or "").strip() or None
    meta_desc = (getattr(doc, "description", None) or "").strip()

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


def build_content_rows_from_links_trafilatura(
    link_rows: list[dict[str, Any]],
    *,
    favor_recall: bool = True,
    min_text_chars: int | None = 200,
) -> tuple[list[dict[str, Any]], int]:
    """
    Parse all link rows with trafilatura; keep rows where `error is None`.
    Returns (content_rows, failed_count).
    """
    out: list[dict[str, Any]] = []
    failed = 0
    for row in link_rows:
        url = row_to_article_url(row)
        parsed = extract_with_trafilatura(
            url,
            favor_recall=favor_recall,
            min_text_chars=min_text_chars,
        )
        if parsed.error is not None:
            failed += 1
            continue
        out.append(link_row_to_content_dict(row, parsed, "trafilatura"))
    return out, failed


def write_daily_content_jsonl_from_links_file_trafilatura(
    links_jsonl_path: Path,
    *,
    content_dir: Path | None = None,
    favor_recall: bool = True,
    min_text_chars: int | None = 200,
) -> tuple[Path, int, int]:
    """
    Build `raw/content/<run_date>.jsonl` from `raw/links/<run_date>.jsonl` using trafilatura.

    Only writes rows where extraction result has `error is None`.
    """
    run_date = links_jsonl_path.stem
    root = links_jsonl_path.parent.parent
    out_dir = content_dir or (root / "content")
    out_path = out_dir / f"{run_date}.jsonl"

    link_rows = load_link_rows_from_jsonl(links_jsonl_path)
    content_rows, failed = build_content_rows_from_links_trafilatura(
        link_rows,
        favor_recall=favor_recall,
        min_text_chars=min_text_chars,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in content_rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    return out_path, len(content_rows), failed
