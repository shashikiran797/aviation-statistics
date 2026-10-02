# DGCA statistics: crawl -> structure -> query

Goal: scrape the data/statistics part of dgca.gov.in, turn it into clean structured tables, and expose a
BI-style UI where anyone can explore and ask questions in plain English.

## Findings from the dry run (2026-10-03)
- Portal URLs like `?page=quat/4268/9661/html` are client-side routes; they return the homepage to a plain GET.
- Navigation data is server-rendered HTML: `jsp/dgca/InventoryList/.../index.html` pages hold `url=`/`data-url=` links.
  Sitemap: `/digigov-portal/jsp/dgca/footerLink/sitemap.jsp`; A-Z index: `.../topHeader/aZIndex/AtoZindex.jsp`.
- Actual pdf/xlsx/zip files are on public S3: `https://public-prd-dgca.s3.ap-south-1.amazonaws.com/<path-after-jsp/dgca/>`.
  The portal host returns 404 for those same paths. No browser automation needed.
- Air traffic statistics layout: `yearly/index.html` -> `<yr>_page1.html` -> `<yr>/Normal/<AIRLINE>.html`
  -> `<AIRLINE>/excel/<table>.xlsx` (+ pdf). Layout differs per year (file naming changes from 2018-19).
- xlsx quirks: 2-row merged headers, legacy-font Hindi title row, abbreviated months, annual row labelled by year.
  Monthly sums equal the annual total, which gives a free validation check.
- Oldest years are zip/PDF only. Accident/Incident reports are dynamic (`viewApplicationDtlsReq`) and need separate work.

## Architecture (static, serverless, everything runs in the browser)
Pipeline (Python, runs in CI) -> versioned Parquet + manifest -> static site (DuckDB-WASM) on any static host.

1. **crawl** (automated, repeatable): discover pages from sitemap + section index pages -> `data/manifest.parquet`
   (url, s3_url, section, year, entity, table_id, ext, etag, content_sha256, first_seen, last_seen, status).
   Rate-limited, retrying, resumable; raw files cached in `data/raw/` keyed by URL.
2. **dedupe** (three layers):
   - File level: skip download when S3 `ETag`/`Last-Modified` unchanged; identical `content_sha256` under different URLs
     (e.g. same table linked from several pages, pdf vs xlsx of the same table) is stored once, extra URLs recorded as aliases.
   - Table level: prefer xlsx over pdf for the same (section, year, entity, table_id); pdf is parsed only if no xlsx exists.
   - Row level: each fact table has a natural key (e.g. airline, fy, segment, month); later reports restating earlier
     years ("last three years" tables) are reconciled: newest publication wins, superseded values kept in `*_history`
     with source file, and disagreements are logged to `data/processed/conflicts.parquet`.
3. **parse**: per-layout deterministic parsers with checksum validation; failures go to a quarantine table.
4. **llm** (build-time, Claude): fallback for PDFs / unknown layouts, schema-constrained output, cached by file hash,
   flagged `parsed_by=llm`, same validators. Never runs in the user's browser.
5. **publish**: write Parquet (one file per fact table, zstd, sorted for row-group pruning), `catalog.json`
   (tables, columns, descriptions, sample questions, provenance), plus a small SQLite/JSON search index.
   Versioned release dir `site/data/v<date>/`; site reads `latest.json`.
6. **web app** (static; no backend):
   - DuckDB-WASM + Parquet over HTTP range requests: the browser downloads only the columns/row groups it needs,
     and queries run locally in a Web Worker. Small dims are cached in IndexedDB / OPFS.
   - UI: filters + charts (Observable Plot or Plotly), table browser with CSV download, SQL editor, per-row source link.
   - **AI query** (three tiers, all local, no API keys, user picks in an "AI setup" panel):
     1. Chrome built-in AI (Prompt API / Gemini Nano, `LanguageModel`): feature-detected; if absent or not downloaded the
        panel shows step-by-step instructions (supported Chrome version, enable the relevant `chrome://flags`, check
        `chrome://on-device-internals` for model status, expected download size, then reload) and a live status check.
     2. WebLLM (WebGPU): opt-in button with explicit size warning (~1-2 GB one-time download, cached by the browser),
        WebGPU detection, progress bar, model choice, and clear "unsupported browser/GPU" messaging.
     3. Neither available: AI tab degrades to curated example questions + SQL editor.
     Both backends share one NL -> SQL pipeline: catalog + schema in the prompt, SQL shown to the user, validated
     (SELECT-only, table allowlist), run in DuckDB-WASM, one self-repair retry on error.
7. **automation**: GitHub Actions cron (weekly): crawl -> diff manifest -> parse only new/changed -> validate -> publish
   to static hosting (GitHub Pages; decided - data expected well under the ~1 GB site cap, revisit if it grows). A run with zero changes commits nothing.

## Local runs and AI triggers
- Everything the CI cron does is runnable locally: `make update` (crawl -> download -> parse -> validate -> publish), each step idempotent
  and resumable; `make test`. The GitHub Actions workflow just calls the same targets.
- AI triggers live in `prompts/` (templates + trigger table in `prompts/README.md`), runnable headless via `make ai-<task>`
  (wraps `claude -p` with scoped `--allowedTools`) or interactively as `/<task>` slash commands in `.claude/commands/`.
  Tasks: fix-parser (quarantined files), extract-pdf, onboard-section, describe-catalog, review-conflicts, nl2sql-eval.
- Pipeline emits a `data/quarantine/` + `.error.json` for anything it cannot parse/validate and prints the exact `make ai-...`
  command to run next. In CI, AI tasks are not run automatically; a failed run opens an issue containing those commands.

## Milestones
- M0 Scaffold + dry-run parser as test fixture (done).
- M1 Crawler + manifest + dedupe for the air-transport statistics section; coverage-by-year report; idempotent re-runs.
- M2 Parse all xlsx in that section (both layouts) with validation; Parquet + catalog publish.
- M3 Static site: DuckDB-WASM, filters/charts, SQL editor, downloads.
- M4 AI query tab (Chrome built-in AI + WebLLM, setup instructions) + eval set of ~30 questions (NL -> expected SQL result); LLM build-time extraction for PDFs.
- M5 CI cron + deploy; expand to other Data & Reports sections one at a time.

## Principles
- Provenance on every row (source URL, file hash, parser). Never silently coerce: unparseable -> quarantine table.
- Parsers are tested against saved fixtures in `tests/fixtures/`.
- Respect the site: low concurrency, identify the client, cache everything.
- Open questions: licence/terms of reuse for DGCA data; data size budget per release.
