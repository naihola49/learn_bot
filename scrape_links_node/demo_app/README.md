# scrape_links demo viewer

Small **FastAPI** app to demo **generated parser code** and the **repair-loop trace** produced when you run `agent_parser.py` with **`-v`** (writes under `raw/debug/codegen/`).

**Not** private model chain-of-thought — the API does not expose that. You’ll see:

- `attempt-NN-parser.py` — normalized model output
- `attempt-NN-feedback.txt` — context for that attempt (initial codegen note, or E2B/validation error passed into repair)

With **`--debug-scrape`**, E2B define/invoke scripts and logs appear under `raw/debug/<date>/<host>/` and are browsable in the **E2B sandbox files** tab.

## Run

From the **repo root** (`personal_agent/`), with deps installed:

```bash
pip install fastapi "uvicorn[standard]"
```

```bash
uvicorn scrape_links_node.demo_app.app:app --reload --port 8765
```

Open **http://127.0.0.1:8765/**

## Scrape first

Example:

```bash
./scrape_links_node/testing/run_scrape.sh \
  --sources-file testing/smoke_sources.txt \
  --max-items-per-source 5 \
  -v \
  --debug-scrape
```

Then refresh the demo page and pick the date / source / attempt.
