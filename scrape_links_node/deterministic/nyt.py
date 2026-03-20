#!/usr/bin/env python3
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

USER_AGENT = "personal-agent-nyt-scrape/0.1"


def fetch_rss(feed_url: str, max_items: int) -> list[dict]:
    req = Request(feed_url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        xml_bytes = resp.read()

    root = ET.fromstring(xml_bytes)
    items: list[dict] = []
    for item in root.findall(".//channel/item"):
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "url": (item.findtext("link") or "").strip(),
                "published_at": (item.findtext("pubDate") or "").strip(),
                "guid": (item.findtext("guid") or "").strip() or (item.findtext("link") or "").strip(),
            }
        )
        if len(items) >= max_items:
            break
    return items
