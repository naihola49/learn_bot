"""
Run model-generated parser code inside an E2B Code Interpreter sandbox.

Flow:
  1) First `run_code`: trusted stdlib prelude (`sandbox_prelude.py`) + model code
     (defines `parse_source` only).
  2) Second `run_code`: host wrapper calls `parse_source` and prints JSON array.

Optional: custom E2B template (deps you baked into the image, e.g. httpx + bs4).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_PRELUDE_PATH = Path(__file__).resolve().with_name("sandbox_prelude.py")
SANDBOX_PRELUDE_SOURCE = _PRELUDE_PATH.read_text(encoding="utf-8")
compile(SANDBOX_PRELUDE_SOURCE, str(_PRELUDE_PATH), "exec")  # fail fast if prelude breaks


def ensure_generated_code_compiles(source: str) -> None:
    """
    Validate syntax on the host before starting E2B (saves sandbox round-trips).
    Surfaces the same class of errors as 'E2B define phase failed' but immediately.
    """
    try:
        compile(source, "<generated_parser.py>", "exec")
    except (SyntaxError, TabError) as e:
        lineno = getattr(e, "lineno", None) or 0
        offset = getattr(e, "offset", None) or 0
        line_txt = (getattr(e, "text", None) or "").rstrip()
        raise RuntimeError(
            "Host syntax check: generated code is not valid Python (not sent to E2B).\n"
            f"  {e.__class__.__name__}: {e.msg} (line {lineno}, offset {offset})\n"
            f"  Source line: {line_txt!r}"
        ) from e


def normalize_generated_code(code: str) -> str:
    """Strip optional markdown fences (``` / ```python) from model output."""
    text = code.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def build_sandbox_define_block(generated_code: str) -> str:
    """Trusted prelude (stdlib) + normalized model output; executed as E2B define cell."""
    body = normalize_generated_code(generated_code)
    return f"{SANDBOX_PRELUDE_SOURCE}\n\n{body}\n"


def _build_invoke_script(source_url: str, max_items: int) -> str:
    # Second cell shares the Code Interpreter kernel with the first cell.
    return (
        "import json\n"
        f"_SOURCE = {json.dumps(source_url)}\n"
        f"_LIMIT = {int(max_items)}\n"
        "_rows = parse_source(_SOURCE, _LIMIT)\n"
        "if not isinstance(_rows, list):\n"
        "    raise TypeError('parse_source must return list, got ' + str(type(_rows)))\n"
        "print(json.dumps(_rows))\n"
    )


def _execution_error_message(execution: Any) -> str:
    parts: list[str] = []
    if execution.error:
        parts.append(f"{execution.error.name}: {execution.error.value}")
        if execution.error.traceback:
            parts.append(execution.error.traceback)
    out = "".join(execution.logs.stdout).strip()
    err = "".join(execution.logs.stderr).strip()
    if out:
        parts.append(f"stdout: {out[:4000]}")
    if err:
        parts.append(f"stderr: {err[:4000]}")
    return "\n".join(parts) if parts else repr(execution)


def _stdout_text(execution: Any) -> str:
    raw = "".join(execution.logs.stdout)
    if not raw.strip() and getattr(execution, "text", None):
        return str(execution.text)
    return raw


def parse_json_array_from_stdout(stdout: str) -> list[Any]:
    """
    Parse a JSON array from sandbox stdout. Tolerates extra log lines by
    scanning for the outermost [...] block.
    """
    text = stdout.strip()
    if not text:
        raise ValueError("empty stdout")

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        chunk = text[start : end + 1]
        data = json.loads(chunk)
        if isinstance(data, list):
            return data

    raise ValueError(f"no JSON array found in stdout (first 400 chars): {text[:400]!r}")


def _slug_from_url(url: str) -> str:
    host = (urlparse(url).netloc or "unknown").lower()
    host = re.sub(r"[^a-z0-9.-]+", "-", host).strip("-") or "unknown"
    return host[:80]


def _write_debug(
    debug_root: Path | None,
    source_url: str,
    attempt: int,
    phase: str,
    script_text: str,
    execution: Any | None,
) -> None:
    if debug_root is None:
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = debug_root / day / _slug_from_url(source_url)
    base.mkdir(parents=True, exist_ok=True)
    stem = f"attempt-{attempt:02d}-{phase}"
    (base / f"{stem}.py").write_text(script_text, encoding="utf-8")
    if execution is not None:
        (base / f"{stem}.log.txt").write_text(
            _execution_error_message(execution), encoding="utf-8"
        )


def run_generated_parser_in_e2b(
    api_key: str,
    code: str,
    source_url: str,
    max_items: int,
    *,
    template: str | None = None,
    sandbox_timeout_sec: int = 300,
    define_timeout_sec: float = 90.0,
    invoke_timeout_sec: float = 180.0,
    debug_dir: Path | None = None,
    attempt_index: int = 0,
) -> list[dict[str, Any]]:
    """
    Two-step execution in one sandbox session:
      1) User code (imports + def parse_source)
      2) Host wrapper invokes parse_source and prints JSON list
    """
    try:
        from e2b_code_interpreter import Sandbox
    except ImportError as e:
        exe = sys.executable
        raise RuntimeError(
            "Cannot import e2b_code_interpreter — the package is missing for THIS Python.\n"
            f"  sys.executable = {exe}\n"
            f"  Install into the same interpreter you use to run agent_parser.py:\n"
            f"    {exe} -m pip install e2b-code-interpreter\n"
            "  Or from repo root:\n"
            f"    {exe} -m pip install -r requirements.txt\n"
            "Tip: `which python3` while your venv is activated should match the path above."
        ) from e

    define_block = build_sandbox_define_block(code)
    ensure_generated_code_compiles(define_block)
    invoke_block = _build_invoke_script(source_url, max_items)

    create_kw: dict[str, Any] = {
        "api_key": api_key,
        "timeout": sandbox_timeout_sec,
        "allow_internet_access": True,
    }
    if template:
        create_kw["template"] = template

    with Sandbox.create(**create_kw) as sandbox:
        # Step 1: prelude + parse_source
        ex1 = sandbox.run_code(define_block, timeout=define_timeout_sec)
        _write_debug(debug_dir, source_url, attempt_index, "define", define_block, ex1)
        if ex1.error:
            raise RuntimeError(
                "E2B define phase failed:\n" + _execution_error_message(ex1)
            )

        # Step 2: invoke
        ex2 = sandbox.run_code(invoke_block, timeout=invoke_timeout_sec)
        _write_debug(debug_dir, source_url, attempt_index, "invoke", invoke_block, ex2)
        if ex2.error:
            raise RuntimeError(
                "E2B invoke phase failed:\n" + _execution_error_message(ex2)
            )

        raw_out = _stdout_text(ex2).strip()
        if not raw_out:
            raise RuntimeError(
                "E2B invoke produced no stdout.\n" + _execution_error_message(ex2)
            )

        try:
            data = parse_json_array_from_stdout(raw_out)
        except (json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(
                f"Bad JSON from sandbox: {e}. stdout head: {raw_out[:600]!r}"
            ) from e

        if not isinstance(data, list):
            raise RuntimeError(f"Sandbox JSON must be a list, got {type(data).__name__}")

        # Normalize to list[dict] for downstream
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                out.append(item)
            else:
                raise RuntimeError(f"List item must be dict, got {type(item).__name__}")

        return out
