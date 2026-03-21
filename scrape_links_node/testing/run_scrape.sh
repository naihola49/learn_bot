#!/usr/bin/env bash
# Always use repo venv Python (avoids Homebrew python3 on PATH).
set -euo pipefail
# Script lives in scrape_links_node/testing/ — repo root is two levels up.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VPY="$ROOT/review_venv/bin/python"
if [[ ! -x "$VPY" ]]; then
  echo "Expected venv at $VPY — create it or fix path." >&2
  exit 1
fi
exec "$VPY" "$ROOT/scrape_links_node/agent_parser.py" "$@"


# to run from root dir: 

# ./scrape_links_node/testing/run_scrape.sh --sources-file testing/smoke_sources.txt --max-items-per-source 5 -v --debug-scrape
