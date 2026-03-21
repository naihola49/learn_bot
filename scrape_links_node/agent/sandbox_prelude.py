# Trusted prelude: copied verbatim before model-generated code in the E2B sandbox.

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

_DEFAULT_UA = "Mozilla/5.0 (compatible; personal-agent-prelude/1.0; +https://example.invalid)"


def fetch_url(url: str, timeout: float = 20.0) -> Optional[bytes]:
    """GET url; return response body or None on failure."""
    try:
        req = Request(url, headers={"User-Agent": _DEFAULT_UA})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def fetch_text(url: str, timeout: float = 20.0) -> Optional[str]:
    """GET url and decode as UTF-8 (replace errors); None on failure."""
    raw = fetch_url(url, timeout=timeout)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def site_origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _xml_local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _gather_locs(root: ET.Element) -> list[str]:
    out: list[str] = []
    for el in root.iter():
        if _xml_local_name(el.tag) == "loc" and el.text:
            t = el.text.strip()
            if t:
                out.append(t)
    return out


def collect_sitemap_page_urls(
    seed_url: str,
    max_urls: int = 500,
    max_depth: int = 8,
) -> list[str]:
    """
    BFS sitemap / sitemapindex. Returns page URLs from urlsets (not sub-sitemap URLs).
    """
    pages: list[str] = []
    q: deque[tuple[str, int]] = deque([(seed_url, 0)])
    seen: set[str] = set()
    while q and len(pages) < max_urls:
        u, depth = q.popleft()
        if depth > max_depth or u in seen:
            continue
        seen.add(u)
        body = fetch_url(u)
        if not body:
            continue
        try:
            root = ET.fromstring(body)
        except Exception:
            continue
        root_name = _xml_local_name(root.tag)
        locs = _gather_locs(root)
        if root_name == "sitemapindex":
            for sub in locs:
                if sub not in seen:
                    q.append((sub, depth + 1))
        else:
            for loc in locs:
                if loc not in pages:
                    pages.append(loc)
                    if len(pages) >= max_urls:
                        return pages
    return pages


def items_from_feed_xml(data: bytes) -> list[dict[str, str]]:
    """
    Parse RSS 2.0 or Atom; return dicts with title, url, published_at, guid.
    """
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(data)
    except Exception:
        return items

    tag = _xml_local_name(root.tag).lower()
    if tag == "rss":
        channel = root.find("channel")
        if channel is None:
            return items
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link).strip()
            pub = (item.findtext("pubDate") or "").strip()
            if link:
                items.append(
                    {
                        "title": title,
                        "url": link,
                        "published_at": pub,
                        "guid": guid or link,
                    }
                )
        return items

    if tag == "feed":
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            guid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
            pub = (
                entry.findtext("a:published", default="", namespaces=ns)
                or entry.findtext("a:updated", default="", namespaces=ns)
                or ""
            ).strip()
            url = ""
            for link_el in entry.findall("a:link", ns):
                rel = (link_el.get("rel") or "alternate").lower()
                href = (link_el.get("href") or "").strip()
                if rel == "alternate" and href:
                    url = href
                    break
            if not url:
                for link_el in entry.findall("a:link", ns):
                    href = (link_el.get("href") or "").strip()
                    if href:
                        url = href
                        break
            if url:
                items.append(
                    {
                        "title": title,
                        "url": url,
                        "published_at": pub,
                        "guid": guid or url,
                    }
                )
        return items

    return items


def try_feed_at_url(url: str) -> list[dict[str, str]]:
    """Fetch URL and parse as RSS/Atom; empty list if not a feed or error."""
    data = fetch_url(url)
    if not data:
        return []
    if not _looks_like_xml(data):
        return []
    got = items_from_feed_xml(data)
    return got


def _looks_like_xml(data: bytes) -> bool:
    s = data.lstrip()[:200].lower()
    return s.startswith(b"<")


class _AlternateFeedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "link":
            return
        ad = {k.lower(): (v or "") for k, v in attrs}
        rel = ad.get("rel", "").lower()
        if "alternate" not in rel.split():
            return
        typ = ad.get("type", "").lower()
        href = ad.get("href", "").strip()
        if not href:
            return
        if "rss" in typ or "atom" in typ or "xml" in typ:
            self.hrefs.append((href, typ))


def discover_feed_urls_from_html(html: str, base_url: str) -> list[str]:
    """Find <link rel=alternate type=rss|atom href=...> and return absolute URLs."""
    p = _AlternateFeedParser()
    try:
        p.feed(html)
    except Exception:
        pass
    out: list[str] = []
    for href, _ in p.hrefs:
        abs_u = urljoin(base_url, href)
        if abs_u not in out:
            out.append(abs_u)
    return out


def common_feed_candidate_urls(page_url: str) -> list[str]:
    """Typical feed paths on the site origin of page_url."""
    origin = site_origin(page_url)
    paths = ["/feed", "/rss.xml", "/atom.xml", "/feeds/all.rss", "/rss", "/feed.xml"]
    return [urljoin(origin.rstrip("/") + "/", path.lstrip("/")) for path in paths]


def same_hostname(url: str, base_url: str) -> bool:
    a = urlparse(url).netloc.lower()
    b = urlparse(base_url).netloc.lower()
    return bool(a) and a == b


_SKIP_HINTS = (
    "/tag/",
    "/category/",
    "/author/",
    "/login",
    "mailto:",
    "#",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".gif",
    ".svg",
    "javascript:",
)


def collect_article_link_urls(html: str, page_url: str, max_links: int) -> list[str]:
    """
    Rough same-host http(s) links from href=... (regex). Filter obvious junk.
    """
    found: list[str] = []
    for m in re.finditer(r"""href\s*=\s*["']([^"'#]+)["']""", html, re.I):
        raw = m.group(1).strip()
        if not raw or any(h in raw.lower() for h in _SKIP_HINTS):
            continue
        abs_u = urljoin(page_url, raw)
        if not abs_u.startswith(("http://", "https://")):
            continue
        if not same_hostname(abs_u, page_url):
            continue
        if abs_u in found:
            continue
        found.append(abs_u)
        if len(found) >= max_links * 4:
            break
    return found


def item_dict(
    url: str,
    title: str = "",
    published_at: str = "",
    guid: str = "",
) -> dict[str, str]:
    g = guid.strip() or url.strip()
    return {
        "title": title.strip(),
        "url": url.strip(),
        "published_at": published_at.strip(),
        "guid": g,
    }


def strip_common_tracking_params(url: str) -> str:
    """Remove common tracking query params when safe (string heuristic)."""
    try:
        p = urlparse(url)
        if not p.query:
            return url
        drop = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
        }
        pairs = []
        for part in p.query.split("&"):
            if not part or "=" not in part:
                continue
            k, _, _ = part.partition("=")
            if k.lower() in drop:
                continue
            pairs.append(part)
        new_q = "&".join(pairs)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, ""))
    except Exception:
        return url
