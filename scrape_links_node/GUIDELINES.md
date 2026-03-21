# Codegen contract (sent to Claude)

**Human runbook:** `OPERATORS.md` · **Downstream JSONL rows:** `OUTPUT_SCHEMA.md`

---

The host **prepends a trusted stdlib prelude** before your code in the same E2B cell. It defines **HTTP/RSS/sitemap helpers** — **use them; do not reimplement** `urllib` + `ElementTree` sitemap walking (that causes syntax errors).

Then **two cells**: (1) prelude + your code defines **`parse_source` only** — no top-level execution; (2) host calls `parse_source(source_url, max_items)` and prints `json.dumps(rows)`.

## Prelude API (already in scope — **no import**)

| Function | Purpose |
|----------|---------|
| `fetch_url(url, timeout=20)` | `GET` → `bytes` or `None` |
| `fetch_text(url)` | `GET` → decoded `str` or `None` |
| `site_origin(url)` | `scheme://netloc` |
| `try_feed_at_url(url)` | Fetch + parse RSS/Atom → `list[dict]` or `[]` |
| `items_from_feed_xml(data: bytes)` | Parse RSS/Atom bytes → items |
| `discover_feed_urls_from_html(html, base_url)` | `<link rel=alternate …>` feed URLs |
| `common_feed_candidate_urls(page_url)` | `/feed`, `/rss.xml`, … on same host |
| `collect_sitemap_page_urls(seed_url, max_urls=500, max_depth=8)` | BFS sitemap / index → page URLs |
| `collect_article_link_urls(html, page_url, max_links)` | Same-host `href` candidates (heuristic) |
| `same_hostname(url, base_url)` | Compare netlocs |
| `item_dict(url, title="", published_at="", guid="")` | Build one output row dict |
| `strip_common_tracking_params(url)` | Drop common `utm_*`, `fbclid`, etc. |

Implement **`parse_source`** by **orchestrating** these (try feed URL → HTML discovery → common paths → `/sitemap.xml` via `urljoin(site_origin(source_url), "sitemap.xml")` → HTML link fallback). Keep your code **short**; prefer **early `return`** over deep nesting.

## `parse_source` return value

- **`list` of `dict`**, length ≤ `max_items`.
- Keys: **`title`**, **`url`**, **`published_at`**, **`guid`** (strings).
- **`url`**: non-empty absolute **`http(s)`**; use `strip_common_tracking_params` when helpful.
- Return **`[]`** only if nothing works.

## Libraries & imports

- Follow the **library policy** in the same user message (stdlib-only vs httpx/bs4 if enabled).
- **Prelude is stdlib-only.** Your extra imports must match the policy.
- No `pip install`. **Never** `import e2b_*`.

## Code shape

- Output **code only** (no markdown fences).
- Define exactly: `def parse_source(source_url: str, max_items: int) -> list[dict]:`
- **Valid Python:** complete `try`/`except`/`finally`; shallow nesting.

## Hygiene

- Do not embed secrets.
