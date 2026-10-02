---
description: DGCA AI task onboard-section (see prompts/README.md)
argument-hint: file or section
---

Task: the crawler found a data section with no registered layout: $ARGUMENTS.
Steps:
1. Read docs/PLAN.md. List this section's manifest rows (data/manifest.parquet) and fetch 3-5 representative files across years.
2. Describe the layout(s): entities, periods, table ids, units, formats (xlsx/pdf/html), year-to-year changes.
3. Propose in docs/sections/$ARGUMENTS.md: fact tables, natural keys (for dedupe), validation checks, parse order.
4. Implement the crawl rules (src/dgca_stats/crawl/) and one deterministic parser for the most common layout, with fixture + test.
Do not bulk-download; use at most 5 files. Report the proposal and what remains.
