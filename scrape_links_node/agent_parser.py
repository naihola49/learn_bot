#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# Allow `import agent` when running `python3 agent_parser.py` from scrape_links_node.
_SCRAPE_ROOT = Path(__file__).resolve().parent
if str(_SCRAPE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPE_ROOT))

from agent.sandbox_scraper import (  # noqa: E402
    normalize_generated_code,
    run_generated_parser_in_e2b,
)
from agent.validation import validate_parsed_rows  # noqa: E402


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Required by Anthropic; override with ANTHROPIC_API_VERSION if they ship a newer header.
_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
# Claude requires max_tokens; large enough for generated parsers + repairs.
_DEFAULT_ANTHROPIC_MAX_TOKENS = 2048


def load_env_from_file(path: Path) -> None:
    """
    Load KEY=value pairs into os.environ (setdefault: existing env wins).
    Supports optional `export ` prefix and single-quoted or double-quoted values.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_anthropic_api_key(explicit_key: str | None) -> str:
    """Priority: --api-key, then ANTHROPIC_API_KEY (e.g. after load_env_from_file)."""
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def resolve_e2b_api_key(explicit_key: str | None) -> str:
    """Priority: --e2b-api-key, then E2B_API_KEY."""
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    return os.getenv("E2B_API_KEY", "").strip()


def read_sources(path: Path) -> list[str]:
    sources: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sources.append(line)
    return sources


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Host suffix → deterministic adapter in `deterministic/`. All other hosts → unknown (Anthropic + E2B).
_DETERMINISTIC_HOST_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("nytimes.com", "nyt"),
    ("medium.com", "medium"),
    ("substack.com", "substack"),
)


def detect_source_type(source: str) -> str:
    host = (urlparse(source).netloc or "").lower()
    for suffix, kind in _DETERMINISTIC_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return kind
    return "unknown"


def normalize_row(source: str, row: dict[str, Any], fetch_method: str) -> dict[str, Any]:
    return {
        "source": source,
        "title": str(row.get("title", "") or "").strip(),
        "url": str(row.get("url", "") or "").strip(),
        "published_at": str(row.get("published_at", "") or "").strip(),
        "guid": str(row.get("guid", "") or "").strip(),
        "fetch_status": "ok",
        "fetch_method": fetch_method,
        "error": "",
        "run_at": utc_now_iso(),
    }


def failure_row(source: str, fetch_method: str, error: str) -> dict[str, Any]:
    return {
        "source": source,
        "title": "",
        "url": "",
        "published_at": "",
        "guid": "",
        "fetch_status": "failed",
        "fetch_method": fetch_method,
        "error": error[:500],
        "run_at": utc_now_iso(),
    }


def _source_slug(url: str) -> str:
    host = (urlparse(url).netloc or "unknown").lower()
    host = re.sub(r"[^a-z0-9.-]+", "-", host).strip("-") or "unknown"
    return host[:80]


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(f"[scrape] {message}", file=sys.stderr, flush=True)


def _is_host_e2b_import_failure(exc: BaseException) -> bool:
    """
    True when the host process cannot load the E2B SDK (wrong venv / wrong python3).
    False when the sandbox started and failed inside user code — those should go through repair.
    """
    msg = str(exc)
    if "E2B define phase failed" in msg or "E2B invoke phase failed" in msg:
        return False
    if "Host dependency missing" in msg:
        return True
    if "Cannot import e2b_code_interpreter" in msg and "sys.executable" in msg:
        return True
    if msg.strip().startswith("Missing e2b-code-interpreter"):
        return True
    return False


def ensure_e2b_host_dependency() -> None:
    """
    Fail fast before Anthropic codegen if this interpreter cannot import the E2B SDK.
    Common mistake: shell shows (review_venv) but `python3` is still Homebrew/system.
    """
    if importlib.util.find_spec("e2b_code_interpreter") is not None:
        return
    exe = sys.executable
    raise RuntimeError(
        "Host dependency missing: e2b-code-interpreter is not installed for THIS Python.\n"
        f"  sys.executable = {exe}\n"
        f"  Install: {exe} -m pip install e2b-code-interpreter\n"
        f"  Or:      {exe} -m pip install -r requirements.txt  (repo root)\n"
        "Check alignment: run `which python3` — it should be inside your venv (…/bin/python3).\n"
        "Or invoke explicitly: ../review_venv/bin/python agent_parser.py …"
    )


def _write_codegen_artifacts(
    enabled: bool,
    codegen_root: Path,
    run_date: str,
    source_url: str,
    attempt: int,
    code: str,
    feedback_for_this_attempt: str,
) -> Path | None:
    """
    Save parser source + the feedback context for this attempt (repair loop trace).
    feedback_for_this_attempt: human-readable note (e.g. prior E2B error or validation msg).
    """
    if not enabled:
        return None
    base = codegen_root / run_date / _source_slug(source_url)
    base.mkdir(parents=True, exist_ok=True)
    stem = f"attempt-{attempt:02d}"
    py_path = base / f"{stem}-parser.py"
    ctx_path = base / f"{stem}-feedback.txt"
    # Match what E2B runs (strip ``` fences from model output).
    py_path.write_text(normalize_generated_code(code), encoding="utf-8")
    ctx_path.write_text(feedback_for_this_attempt.strip() + "\n", encoding="utf-8")
    return base


def _library_policy_text(rich_deps: bool) -> str:
    if rich_deps:
        return (
            "Libraries: you MAY use httpx, bs4 (BeautifulSoup), lxml, and the Python standard library.\n"
            "A stdlib PRELUDE is still prepended (fetch_url, try_feed_at_url, collect_sitemap_page_urls, …); prefer it when enough.\n"
            "Prefer httpx for HTTP when you need it (timeouts, follow_redirects=True, User-Agent).\n"
            "Do not pip install at runtime; only use what is already in the sandbox template.\n"
        )
    return (
        "Libraries: a trusted stdlib PRELUDE runs before your code (fetch_url, feeds, sitemap helpers). "
        "Use those functions; do not rewrite low-level sitemap or RSS XML walking.\n"
        "You may use the Python standard library as needed (e.g. urljoin, urlparse).\n"
        "Do not import httpx, bs4, requests, or lxml unless the host enabled --rich-sandbox-deps.\n"
    )


def _tiered_strategy_text() -> str:
    return (
        "Extraction strategy (use PRELUDE helpers; try in order, stop when you have enough):\n"
        "1) try_feed_at_url(source_url) — if already a feed.\n"
        "2) html = fetch_text(source_url); if html, discover_feed_urls_from_html(html, source_url) then try_feed_at_url on each.\n"
        "3) For u in common_feed_candidate_urls(source_url): try_feed_at_url(u).\n"
        "4) sm = urljoin(site_origin(source_url), 'sitemap.xml'); collect_sitemap_page_urls(sm, max_urls=max_items*20); "
        "map to rows with item_dict(url) and trim to max_items.\n"
        "5) If still short: reuse html from (2) or fetch_text(source_url); collect_article_link_urls(html, source_url, max_items*4); "
        "keep article-like paths; item_dict(u) until max_items.\n"
        "Apply strip_common_tracking_params to urls when appropriate.\n"
        "Each returned dict MUST have non-empty absolute http(s) \"url\".\n"
    )


def _anthropic_text_from_response(data: dict[str, Any]) -> str:
    """Extract assistant text from Messages API JSON (concatenate text blocks)."""
    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise RuntimeError(
            "Anthropic returned no text content in message.content. "
            f"Raw (truncated): {json.dumps(data, ensure_ascii=True)[:800]}"
        )
    return text


def _call_anthropic_messages(
    api_key: str,
    model: str,
    *,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int = _DEFAULT_ANTHROPIC_MAX_TOKENS,
) -> str:
    version = (os.getenv("ANTHROPIC_API_VERSION") or _DEFAULT_ANTHROPIC_VERSION).strip()
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": temperature,
    }
    req = Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": version,
        },
        method="POST",
    )
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _anthropic_text_from_response(data)


def call_anthropic_for_parser(
    api_key: str,
    source: str,
    guidelines_text: str,
    max_items_per_source: int,
    model: str,
    *,
    rich_deps: bool = False,
) -> str:
    system_prompt = (
        "You are a code generator for web source parsers.\n"
        "You MUST follow the provided GUIDELINES exactly.\n"
        "The sandbox runs TWO cells: (1) a fixed stdlib PRELUDE plus your code defines parse_source only — no top-level calls; "
        "(2) the host invokes parse_source and prints JSON.\n"
        "Use prelude helpers (fetch_url, try_feed_at_url, collect_sitemap_page_urls, etc.); do not reimplement sitemap BFS or RSS parsing.\n"
        "NEVER import e2b_code_interpreter, e2b, or any E2B SDK inside parse_source — "
        "the host already runs the sandbox; your code only orchestrates prelude + policy.\n"
        "Generated code will run in an isolated E2B cloud sandbox with internet access.\n"
        "Return only parse_source plus any small imports you still need (prelude needs no import).\n"
        "Do not reference undefined names.\n"
        "Every try/except must be complete (no bare else after an incomplete try).\n"
        "If you use HTMLParser, use: from html.parser import HTMLParser\n"
        "Return only Python code defining:\n"
        "def parse_source(source_url: str, max_items: int) -> list[dict]:\n"
        "Each dict: title (str), url (str, REQUIRED non-empty absolute http(s)), published_at (str), guid (str).\n"
        "No markdown, no explanation, code only."
    )
    user_prompt = (
        f"GUIDELINES:\n{guidelines_text}\n\n"
        f"{_library_policy_text(rich_deps)}"
        f"{_tiered_strategy_text()}\n"
        f"Generate parser code for source: {source}\n"
        f"- Respect max_items={max_items_per_source}.\n"
        f"- Return [] only if every strategy fails; prefer returning real article URLs.\n"
    )
    return _call_anthropic_messages(
        api_key,
        model,
        system=system_prompt,
        user=user_prompt,
        temperature=0.2,
    )


def repair_parser_code(
    api_key: str,
    source: str,
    guidelines_text: str,
    max_items_per_source: int,
    model: str,
    previous_code: str,
    error_message: str,
    *,
    rich_deps: bool = False,
) -> str:
    system_prompt = (
        "You are repairing Python parser code.\n"
        "Return corrected code only.\n"
        "Sandbox uses two cells: first cell defines parse_source only; second cell calls it.\n"
        "Code runs in an E2B cloud sandbox with internet access.\n"
        "Never import e2b_code_interpreter or e2b — not available in the sandbox.\n"
        "The prelude is still prepended; fix parse_source to use prelude helpers and valid syntax.\n"
        "Add imports only if required by the library policy.\n"
        "Fix any SyntaxError (complete try/except/finally; else must match if/try/for/while).\n"
        "Do not reference undefined names.\n"
        "Every successful parse must yield dicts with non-empty absolute http(s) url strings.\n"
        f"{_library_policy_text(rich_deps)}"
    )
    user_prompt = (
        f"GUIDELINES:\n{guidelines_text}\n\n"
        f"{_tiered_strategy_text()}\n"
        f"Source: {source}\n"
        f"max_items={max_items_per_source}\n"
        f"Problem (runtime OR validation):\n{error_message}\n\n"
        "Fix this code and return only corrected Python code:\n"
        f"{previous_code}"
    )
    return _call_anthropic_messages(
        api_key,
        model,
        system=system_prompt,
        user=user_prompt,
        temperature=0.0,
    )


def run_known_adapter(
    source_type: str,
    source: str,
    max_items_per_source: int,
    deterministic_dir: Path,
) -> list[dict[str, Any]]:
    if source_type == "substack":
        mod = load_module(deterministic_dir / "substack.py", "substack")
        entries = mod.substack_posts(source, max_items_per_source)
        return [normalize_row(source, e, "substack_api") for e in entries]
    if source_type == "medium":
        mod = load_module(deterministic_dir / "medium.py", "medium")
        feed_url = mod.to_medium_feed(source)
        entries = mod.fetch_rss(feed_url, max_items_per_source)
        return [normalize_row(source, e, "medium_rss") for e in entries]
    if source_type == "nyt":
        mod = load_module(deterministic_dir / "nyt.py", "nyt")
        entries = mod.fetch_rss(source, max_items_per_source)
        return [normalize_row(source, e, "nyt_rss") for e in entries]
    return []


def write_jsonl(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic parser orchestrator")
    parser.add_argument("--sources-file", default="files.txt")
    parser.add_argument("--guidelines-file", default="GUIDELINES.md")
    parser.add_argument("--max-items-per-source", type=int, default=10)
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Anthropic model id (Messages API).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (overrides env and .env.secret)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Dotenv file to load into os.environ first (default: <repo>/.env.secret)",
    )
    parser.add_argument(
        "--e2b-api-key",
        default=None,
        help="E2B API key (overrides env and .env.secret)",
    )
    parser.add_argument(
        "--e2b-template",
        default=None,
        help="E2B sandbox template id (or set E2B_TEMPLATE). Default: SDK code-interpreter template.",
    )
    parser.add_argument(
        "--rich-sandbox-deps",
        action="store_true",
        help="Allow httpx/bs4/lxml in generated code (needs matching E2B template). "
        "Or set E2B_SCRAPE_RICH_DEPS=1.",
    )
    parser.add_argument(
        "--min-valid-urls",
        type=int,
        default=1,
        help="Minimum rows with valid http(s) URLs required per unknown source (default: 1).",
    )
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=2,
        help="Max codegen→sandbox→validate attempts per unknown source (default: 1 = initial only; use 2+ to allow repairs).",
    )
    parser.add_argument(
        "--debug-scrape",
        action="store_true",
        help="Write per-attempt sandbox scripts/logs under raw/debug/ (gitignored).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Log progress to stderr; save generated parser + repair feedback under raw/debug/codegen/ (gitignored).",
    )
    args = parser.parse_args()

    module_dir = Path(__file__).resolve().parent
    env_path = Path(args.env_file).expanduser() if args.env_file else module_dir.parent / ".env.secret"
    load_env_from_file(env_path)

    sources_file = module_dir / args.sources_file
    guidelines_file = module_dir / args.guidelines_file
    if not sources_file.exists():
        raise FileNotFoundError(f"Sources file not found: {sources_file}")
    if not guidelines_file.exists():
        raise FileNotFoundError(f"Guidelines file not found: {guidelines_file}")

    sources = read_sources(sources_file)
    guidelines_text = guidelines_file.read_text(encoding="utf-8")
    api_key = resolve_anthropic_api_key(args.api_key)
    e2b_key = resolve_e2b_api_key(args.e2b_api_key)
    e2b_template = (args.e2b_template or os.getenv("E2B_TEMPLATE") or "").strip() or None
    rich_deps = args.rich_sandbox_deps or os.getenv("E2B_SCRAPE_RICH_DEPS", "").lower() in (
        "1",
        "true",
        "yes",
    )
    debug_dir: Path | None = (
        (module_dir.parent / "raw" / "debug") if args.debug_scrape else None
    )
    codegen_root = module_dir.parent / "raw" / "debug" / "codegen"
    deterministic_dir = module_dir / "deterministic"

    run_date = datetime.now().strftime("%Y-%m-%d")
    out_path = module_dir.parent / "raw" / "links" / f"{run_date}.jsonl"
    rows: list[dict[str, Any]] = []

    _vlog(args.verbose, f"host Python: {sys.executable}")

    for source in sources:
        source_type = detect_source_type(source)
        try:
            _vlog(
                args.verbose,
                f"source={source!r} detected_type={source_type!r} max_items={args.max_items_per_source}",
            )
            known_rows = run_known_adapter(
                source_type, source, args.max_items_per_source, deterministic_dir
            )
            if known_rows:
                _vlog(
                    args.verbose,
                    f"deterministic adapter returned {len(known_rows)} row(s), skipping LLM/E2B",
                )
                rows.extend(known_rows)
                continue

            if not api_key:
                rows.append(failure_row(source, "anthropic_api", "ANTHROPIC_API_KEY not set"))
                _vlog(args.verbose, "skip: ANTHROPIC_API_KEY not set")
                continue

            if not e2b_key:
                rows.append(failure_row(source, "e2b_sandbox", "E2B_API_KEY not set"))
                _vlog(args.verbose, "skip: E2B_API_KEY not set")
                continue

            ensure_e2b_host_dependency()

            _vlog(args.verbose, "unknown source: Anthropic initial codegen…")
            code = call_anthropic_for_parser(
                api_key=api_key,
                source=source,
                guidelines_text=guidelines_text,
                max_items_per_source=args.max_items_per_source,
                model=args.model,
                rich_deps=rich_deps,
            )
            parsed_rows: list[dict[str, Any]] = []
            max_attempts = max(1, args.max_repair_attempts)
            succeeded = False
            # How we obtained `code` for this attempt (trace for humans; not model "chain of thought").
            feedback_for_code = (
                "Initial Anthropic (Claude) codegen.\n"
                "Note: chat models do not expose private chain-of-thought; this folder is the repair loop trace.\n"
            )
            for attempt in range(max_attempts):
                snap = _write_codegen_artifacts(
                    args.verbose,
                    codegen_root,
                    run_date,
                    source,
                    attempt,
                    code,
                    feedback_for_code,
                )
                if snap is not None:
                    _vlog(
                        args.verbose,
                        f"saved parser + feedback → {snap}/attempt-{attempt:02d}-*.py|.txt",
                    )

                _vlog(
                    args.verbose,
                    f"E2B attempt {attempt + 1}/{max_attempts} (define + invoke)…",
                )
                try:
                    parsed_rows = run_generated_parser_in_e2b(
                        e2b_key,
                        code,
                        source,
                        args.max_items_per_source,
                        template=e2b_template,
                        debug_dir=debug_dir,
                        attempt_index=attempt,
                    )
                except Exception as exec_exc:
                    err = str(exec_exc)
                    _vlog(
                        args.verbose,
                        "E2B error (excerpt): " + err[:800]
                        + ("…" if len(err) > 800 else ""),
                    )
                    if _is_host_e2b_import_failure(exec_exc):
                        _vlog(
                            args.verbose,
                            "aborting repair: host Python cannot import e2b-code-interpreter "
                            "(wrong interpreter — not a parser bug). Use the venv binary or `which python3`.",
                        )
                        rows.append(failure_row(source, "host_python", err))
                        succeeded = False
                        break
                    if attempt >= max_attempts - 1:
                        rows.append(failure_row(source, "e2b_sandbox", err))
                        succeeded = False
                        break
                    _vlog(args.verbose, "calling Anthropic repair after E2B failure…")
                    code = repair_parser_code(
                        api_key=api_key,
                        source=source,
                        guidelines_text=guidelines_text,
                        max_items_per_source=args.max_items_per_source,
                        model=args.model,
                        previous_code=code,
                        error_message=err,
                        rich_deps=rich_deps,
                    )
                    feedback_for_code = (
                        f"Anthropic repair after E2B failure on attempt {attempt} (0-based).\n\n{err}\n"
                    )
                    continue

                _vlog(
                    args.verbose,
                    f"E2B returned {len(parsed_rows)} dict row(s); validating…",
                )
                ok, verr = validate_parsed_rows(
                    parsed_rows,
                    args.max_items_per_source,
                    min_valid_urls=max(1, args.min_valid_urls),
                )
                if ok:
                    _vlog(args.verbose, "validation OK")
                    succeeded = True
                    break

                sample = json.dumps(parsed_rows[:5], ensure_ascii=True)[:2000]
                vmsg = f"{verr}\nReturned {len(parsed_rows)} rows (sample): {sample}"
                _vlog(
                    args.verbose,
                    "validation failed (excerpt): "
                    + vmsg[:900]
                    + ("…" if len(vmsg) > 900 else ""),
                )
                if attempt >= max_attempts - 1:
                    rows.append(failure_row(source, "e2b_sandbox", vmsg[:500]))
                    succeeded = False
                    break
                _vlog(args.verbose, "calling Anthropic repair after validation failure…")
                code = repair_parser_code(
                    api_key=api_key,
                    source=source,
                    guidelines_text=guidelines_text,
                    max_items_per_source=args.max_items_per_source,
                    model=args.model,
                    previous_code=code,
                    error_message=vmsg,
                    rich_deps=rich_deps,
                )
                feedback_for_code = (
                    f"Anthropic repair after validation failure on attempt {attempt} (0-based).\n\n{vmsg}\n"
                )

            if not succeeded:
                continue
            for entry in parsed_rows:
                rows.append(normalize_row(source, entry, "e2b_sandbox"))
        except Exception as exc:
            rows.append(failure_row(source, "agent_parser", str(exc)))

    write_jsonl(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
