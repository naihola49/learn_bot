"""
Tier 1: article extraction with newspaper3k (download + parse).

Typical inputs: absolute article URLs from `raw/links/*.jsonl` (`url` field), e.g. NYT,
Medium, NBC News, Substack-style hosts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
