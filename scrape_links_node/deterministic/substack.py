#!/usr/bin/env python3
from __future__ import annotations

import json
from urllib.request import Request, urlopen

USER_AGENT = "personal-agent-scrape-links-test/0.1"


def substack_posts(base_url: str, max_items: int) -> list[dict]:
    api_url = f"{base_url.rstrip('/')}/api/v1/posts?offset=0&limit={max_items}"
    req = Request(api_url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=15) as resp:
        posts = json.loads(resp.read().decode("utf-8"))

    entries: list[dict] = []
    for post in posts[:max_items]:
        entries.append(
            {
                "title": (post.get("title") or "").strip(),
                "url": (post.get("canonical_url") or "").strip(),
                "published_at": (post.get("post_date") or "").strip(),
                "guid": str(post.get("id") or post.get("canonical_url") or "").strip(),
            }
        )
    return entries
