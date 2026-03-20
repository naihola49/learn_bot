# Scrape Links Node: Window + Central Dedupe

## Goal
Keep source-specific scrapers simple and move duplicate handling to one shared layer before taxonomy classification.

## Why this design
- Source adapters (Substack, Medium, NYT) should only fetch and normalize basic link metadata.
- Deduping once in a central layer is easier to reason about than per-source dedupe rules.
- A rolling time window supports catch-up if a run is missed, while dedupe avoids repeated downstream analysis.

## Proposed pre-classification flow
1. Fetch links from all sources.
2. Normalize fields (`source`, `title`, `url`, `published_at`, `guid`).
3. Apply a publish-time window (default: last 3 days).
4. Apply centralized dedupe using stable keys.
5. Send deduped rows to taxonomy/BERT node.

## Dedupe key priority
1. `guid` (when trustworthy and present)
2. Canonicalized URL hash (fallback)
3. `source + normalized_title + published_date` hash (last resort)

## State model
- `seen`: link has been fetched at least once
- `processed`: link has already entered downstream analysis
- `presented`: link has been surfaced in a user-facing report

For v1, start with `seen` tracking only. Add `processed` and `presented` after the full pipeline is connected.

## v1 defaults
- `window_days = 3`
- `max_items_per_source = 10`
- Output includes both:
  - full fetched rows
  - deduped rows for downstream processing
