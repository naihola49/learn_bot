"""
Demo FastAPI app: browse `raw/debug/codegen/` (generated parsers + repair feedback)
and optional `raw/debug/<date>/<host>/` E2B scripts/logs from `--debug-scrape`.

Run from repo root:
  uvicorn scrape_links_node.demo_app.app:app --reload --port 8765
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# personal_agent/scrape_links_node/demo_app/app.py
_DEMO_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEMO_DIR.parent.parent
_CODEGEN_ROOT = _REPO_ROOT / "raw" / "debug" / "codegen"
_DEBUG_ROOT = _REPO_ROOT / "raw" / "debug"
_LINKS_DIR = _REPO_ROOT / "raw" / "links"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,79}$")

app = FastAPI(title="scrape_links demo viewer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

static_dir = _DEMO_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _bad_date(s: str) -> bool:
    return not _DATE_RE.match(s)


def _bad_slug(s: str) -> bool:
    return not _SLUG_RE.match(s)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "repo": str(_REPO_ROOT)}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "repo_root": str(_REPO_ROOT),
        "codegen_root": str(_CODEGEN_ROOT),
        "debug_root": str(_DEBUG_ROOT),
        "links_dir": str(_LINKS_DIR),
        "note": (
            "Anthropic does not expose private chain-of-thought; this UI shows "
            "saved generated code and the repair-loop feedback (E2B/validation errors). "
            "Run with `-v` to write codegen artifacts."
        ),
    }


@app.get("/api/codegen/dates")
def codegen_dates() -> list[str]:
    if not _CODEGEN_ROOT.is_dir():
        return []
    out: list[str] = []
    for p in sorted(_CODEGEN_ROOT.iterdir(), reverse=True):
        if p.is_dir() and _DATE_RE.match(p.name):
            out.append(p.name)
    return out


@app.get("/api/codegen/{date}/sources")
def codegen_sources(date: str = PathParam(...)) -> list[str]:
    if _bad_date(date):
        raise HTTPException(400, "Invalid date (expected YYYY-MM-DD)")
    root = _CODEGEN_ROOT / date
    if not root.is_dir():
        raise HTTPException(404, f"No codegen for {date}")
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _attempt_indices_from_parser_globs(root: Path) -> list[int]:
    """Return sorted attempt indices that have attempt-NN-parser.py."""
    found: set[int] = set()
    for p in root.glob("attempt-*-parser.py"):
        m = re.match(r"^attempt-(\d+)-parser\.py$", p.name)
        if m:
            found.add(int(m.group(1), 10))
    return sorted(found)


@app.get("/api/codegen/{date}/{source_slug}/attempts")
def codegen_attempts(
    date: str = PathParam(...),
    source_slug: str = PathParam(...),
) -> dict[str, Any]:
    if _bad_date(date) or _bad_slug(source_slug):
        raise HTTPException(400, "Invalid date or source slug")
    base = _CODEGEN_ROOT / date / source_slug
    if not base.is_dir():
        raise HTTPException(404, "Codegen folder not found")
    attempts: list[dict[str, Any]] = []
    for idx in _attempt_indices_from_parser_globs(base):
        stem = f"attempt-{idx:02d}"
        py_path = base / f"{stem}-parser.py"
        fb_path = base / f"{stem}-feedback.txt"
        attempts.append(
            {
                "index": idx,
                "parser_file": py_path.name,
                "feedback_file": fb_path.name if fb_path.is_file() else None,
                "parser_bytes": py_path.stat().st_size if py_path.is_file() else 0,
            }
        )
    return {"date": date, "source_slug": source_slug, "attempts": attempts}


@app.get("/api/codegen/{date}/{source_slug}/attempt/{attempt_index}/parser")
def codegen_parser_text(
    date: str = PathParam(...),
    source_slug: str = PathParam(...),
    attempt_index: int = PathParam(..., ge=0, le=99),
) -> dict[str, str]:
    if _bad_date(date) or _bad_slug(source_slug):
        raise HTTPException(400, "Invalid date or source slug")
    stem = f"attempt-{attempt_index:02d}"
    path = _CODEGEN_ROOT / date / source_slug / f"{stem}-parser.py"
    if not path.is_file():
        raise HTTPException(404, "Parser file not found")
    return {"content": path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/codegen/{date}/{source_slug}/attempt/{attempt_index}/feedback")
def codegen_feedback_text(
    date: str = PathParam(...),
    source_slug: str = PathParam(...),
    attempt_index: int = PathParam(..., ge=0, le=99),
) -> dict[str, str]:
    if _bad_date(date) or _bad_slug(source_slug):
        raise HTTPException(400, "Invalid date or source slug")
    stem = f"attempt-{attempt_index:02d}"
    path = _CODEGEN_ROOT / date / source_slug / f"{stem}-feedback.txt"
    if not path.is_file():
        raise HTTPException(404, "Feedback file not found")
    return {"content": path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/sandbox/{date}/{source_slug}/files")
def sandbox_files(
    date: str = PathParam(...),
    source_slug: str = PathParam(...),
) -> dict[str, Any]:
    """E2B debug artifacts from `--debug-scrape` (same slug as codegen host)."""
    if _bad_date(date) or _bad_slug(source_slug):
        raise HTTPException(400, "Invalid date or source slug")
    base = _DEBUG_ROOT / date / source_slug
    if not base.is_dir():
        raise HTTPException(404, "No sandbox debug folder (use --debug-scrape)")
    names = sorted(p.name for p in base.iterdir() if p.is_file())
    return {"date": date, "source_slug": source_slug, "files": names}


@app.get("/api/sandbox/{date}/{source_slug}/file/{filename:path}")
def sandbox_file(
    date: str = PathParam(...),
    source_slug: str = PathParam(...),
    filename: str = PathParam(...),
) -> dict[str, str]:
    if _bad_date(date) or _bad_slug(source_slug):
        raise HTTPException(400, "Invalid date or source slug")
    if "/" in filename or ".." in filename or filename.startswith("."):
        raise HTTPException(400, "Invalid filename")
    if not re.match(r"^[a-zA-Z0-9._-]+\.(?:py|txt)$", filename):
        raise HTTPException(400, "Only .py and .txt sandbox files are allowed")
    path = _DEBUG_ROOT / date / source_slug / filename
    root = (_DEBUG_ROOT / date / source_slug).resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise HTTPException(404, "File not found") from None
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return {"content": path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/links/{date}")
def links_jsonl(date: str = PathParam(...)) -> dict[str, Any]:
    if _bad_date(date):
        raise HTTPException(400, "Invalid date")
    path = _LINKS_DIR / f"{date}.jsonl"
    if not path.is_file():
        raise HTTPException(404, f"No links file for {date}")
    lines: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            lines.append({"_raw": line, "_error": "invalid json"})
    return {"date": date, "path": str(path), "rows": lines}


@app.get("/")
def index() -> FileResponse:
    idx = static_dir / "index.html"
    if not idx.is_file():
        raise HTTPException(500, "static/index.html missing")
    return FileResponse(idx)

