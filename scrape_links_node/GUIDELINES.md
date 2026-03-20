# GPT Scrape Agent Guidelines

## Objective
Given a source list, extract article links and metadata into a strict JSONL schema for downstream processing.

## Inputs
- `source_list_file` (text file with one source URL per line, comments allowed with `#`)
- `OPENAI_API_KEY` on the host (code generation / repair only; never embedded in sandbox output)
- `E2B_API_KEY` on the host (starts isolated sandboxes that execute generated fetch/parse code)
- Optional: both keys in repo-root `.env.secret` (gitignored). `agent_parser.py` loads that file into the process environment on startup (keys already exported in the shell still win). Overrides: `--api-key`, `--e2b-api-key`, or `--env-file` for a different dotenv path.

### E2B template & dependencies
- **Default:** E2B Code Interpreter template (`code-interpreter-v1` via SDK). Best for **stdlib-only** parsers (`urllib`, `html.parser`, `xml.etree.ElementTree`).
- **Custom template:** Build an E2B template with extra packages (e.g. `httpx`, `beautifulsoup4`, `lxml`), then set:
  - `E2B_TEMPLATE=<your-template-id>` or `--e2b-template <id>`
  - Enable richer codegen: `--rich-sandbox-deps` or `E2B_SCRAPE_RICH_DEPS=1`
- Generated code must **not** run `pip install` at runtime; only use what the template provides.

### Host Python vs `e2b-code-interpreter` (common pitfall)
- The package must be installed for **the same interpreter** that runs `agent_parser.py` (not only “some” Python on your machine).
- **`GUIDELINES.md` is only sent to the model** — it cannot fix your laptop’s Python. If the error mentions `sys.executable = /opt/homebrew/...`, that is **your Mac’s Homebrew Python**, not the venv: you ran `python3` while the venv was not actually first on `PATH`.
- If your prompt shows `(review_venv)` but you still get import errors, **`python3` may not be the venv’s binary**. Check `which python3` — it must be `…/personal_agent/review_venv/bin/python3`.
- **Reliable:** `../review_venv/bin/python agent_parser.py …` from `scrape_links_node/`, or `python -m pip install -r ../requirements.txt` using that same `python`.
- With **`-v`**, the first log line is `host Python: …` — that path must match where you ran `pip install`.

### Forbidden in generated sandbox code (parser)
- **Do not** `import e2b_code_interpreter`, `e2b`, or any E2B SDK — the host starts the sandbox; your job is HTTP + HTML/XML/RSS only.
- If `raw/debug/codegen/.../feedback.txt` repeats “Missing e2b-code-interpreter” **without** “E2B define phase failed”, that message came from the **host** missing the package, not from NBC’s website. Fix the interpreter; don’t keep “repairing” the parser.

### Robustness flags (host)
- `--min-valid-urls` — minimum rows with valid `http://` or `https://` URLs (default: 1)
- `--max-repair-attempts` — OpenAI repair loops after sandbox or validation failure (default: 5)
- `--debug-scrape` — write per-attempt E2B cell scripts + error logs under `raw/debug/<date>/<host>/` (gitignored)
- **`--verbose` / `-v`** — print progress to **stderr** (timestamps: codegen, E2B attempts, validation, repairs). Also writes **`raw/debug/codegen/<date>/<host>/attempt-NN-parser.py`** and **`attempt-NN-feedback.txt`** (why this version of the parser exists: initial vs prior error/validation text). Chat models do not expose private chain-of-thought; the feedback files are the observable repair trace.

## Required output
Write to:
- `raw/links/<run_date>.jsonl`

Each line must be valid JSON with this schema:
`{source, title, url, published_at, guid, fetch_status, fetch_method, error, run_at}`

Field constraints:
- `source`: original source URL from input list
- `title`: string (may be empty only if URL is still a valid article link)
- `url`: absolute canonical article URL (**required** for successful scrape rows)
- `published_at`: source-provided timestamp string (empty string allowed if unknown)
- `guid`: stable identifier (prefer source guid/id; else canonical URL)
- `fetch_status`: `"ok"` or `"failed"`
- `fetch_method`: e.g. `"rss"`, `"substack_api"`, `"e2b_sandbox"`
- `error`: empty string on success; short error message on failure
- `run_at`: ISO-8601 UTC timestamp

## Extraction policy (generated parsers)
1. Prefer feeds: RSS/Atom, `<link rel="alternate" ...>`, common `/feed` paths.
2. Then sitemaps (`/sitemap.xml`, nested sitemapindex).
3. Then same-host HTML links with heuristics (drop tags, login, static assets).
4. Limit per-source extraction count (`max_items_per_source`).
5. Return **[]** only if all strategies fail; otherwise return dicts with **non-empty absolute http(s) URLs**.

## Sandbox execution model
1. **Cell 1:** imports + `def parse_source(source_url, max_items) -> list[dict]` only (no top-level calls).
2. **Cell 2:** host wrapper calls `parse_source` and prints **one JSON array** to stdout.

## Normalization policy
1. Strip tracking query params when building canonical URL if safe.
2. Trim whitespace in titles and URLs.
3. Keep original `published_at`; normalization can happen in downstream node.

## Safety and reliability
- Never write secrets to output files or logs.
- Never mutate unrelated files.
- Keep output strictly schema-compliant; malformed lines are not allowed.
- On parse/fetch failures, degrade gracefully and keep pipeline running.

## Generated code contract
1. Self-contained, directly executable in the sandbox kernel.
2. Explicit imports for every symbol used.
3. `HTMLParser` → `from html.parser import HTMLParser`
4. Define only `parse_source` in cell 1 (no `if __main__` execution).
5. Do not reference undefined names.
