# Tiered Strategy for content fetch
Avoiding large token costs

## Tiers - Waterfall Strategy
Tier 1: newspaper3k - Done
- Handles NYT, most major publications out of the box
- One line: article.download(); article.parse(); article.text

Tier 2: trafilatura - Done
- Better than newspaper3k on paywalled/complex sites
- Specifically built for content extraction, handles boilerplate removal

Tier 3: Readability + BeautifulSoup
- For sites both above fail on
- Still deterministic, no sandbox needed

Tier 4: E2B
- Only if all three above return empty or garbage content
- At this point you genuinely need dynamic codegen

Looking into Playwright for cookie fetch + store for paywalled websites