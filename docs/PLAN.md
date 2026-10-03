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
   (the semantic model: facts, dimensions, measures, joins, descriptions, sample questions, provenance), plus a small SQLite/JSON search index.
   Versioned release dir `site/data/v<date>/`; site reads `latest.json`.
6. **web app** (static; no backend). Primary interface is a **generic dashboard builder**, not a SQL editor.
   - **Semantic model** (`semantic.yaml` -> `catalog.json`, generated at publish, hand-curated overrides allowed):
     - *Facts*: one per Parquet fact table (e.g. `airline_monthly_traffic`), each with a grain and natural key.
     - *Dimensions*: conformed and shared across facts (`airline`, `date` with fy/quarter/month, `segment` domestic/intl,
       `airport`, `aircraft_type`...), with hierarchies, display labels, and value lists for filter pickers.
     - *Measures*: base columns with unit + default aggregation (sum pax), and derived measures defined once
       (load factor = RPK/ASK, YoY %, share of total). Non-additive measures are flagged so the builder never sums them wrongly.
     - *Joins*: fact -> dimension keys declared in the model; the builder only offers combinations that are valid.
   - **Builder UX** (Metabase/Superset-style): pick a fact -> drag dimensions and measures onto rows/columns/series/color;
     filter panel (multi-select, date range, numeric range, top-N); chart type auto-suggested (line for time, bar for category,
     table/pivot, KPI tile, heatmap, map for airports later) and overridable; sort, limit, compare-to-previous-period.
     A query is a small JSON **chart spec** {fact, dimensions, measures, filters, sort, limit, viz}; a compiler turns it into
     DuckDB SQL, so every widget is introspectable, "view SQL" is a read-only popover, and specs are testable without a browser.
   - **Dashboards**: grid of widgets with global cross-filters (click a bar to filter others), shared filter bar, saved to
     localStorage and exportable/importable as JSON, and shareable via a URL-encoded spec (no server). Curated starter
     dashboards ship with the site (Air traffic overview, Airline comparison, Load factor trends).
   - **Engine**: DuckDB-WASM + Parquet over HTTP range requests (only needed columns/row groups downloaded), queries in a Web
     Worker; small dimensions cached in IndexedDB / OPFS. Charts via Observable Plot (decided; revisit ECharts only if maps/rich interaction are needed). Download any widget as CSV/PNG.
   - **SQL editor**: demoted to an "Advanced" drawer for power users (read-only, SELECT-only, table allowlist), plus "open
     this widget as SQL".
   - **AI assistant** (three local tiers, no API keys, user picks in an "AI setup" panel):
     1. Chrome built-in AI (Prompt API / Gemini Nano, `LanguageModel`): feature-detected; if absent or not downloaded the
        panel shows step-by-step instructions (supported Chrome version, enable the relevant `chrome://flags`, check
        `chrome://on-device-internals` for model status, expected download size, then reload) and a live status check.
     2. WebLLM (WebGPU): opt-in button with explicit size warning (~1-2 GB one-time download, cached by the browser),
        WebGPU detection, progress bar, model choice, and clear "unsupported browser/GPU" messaging.
     3. Neither available: assistant is hidden; the builder and curated example dashboards work fully without AI.
     The model's job is to **emit a chart spec (or edit the current one) against the semantic model**, not free-form SQL:
     "show IndiGo vs Air India passengers by month for FY2017-18" -> spec -> validated against the model (unknown
     fact/dimension/measure rejected, one self-repair retry) -> rendered as an editable widget the user can tweak.
     Small local models do much better choosing from a closed vocabulary than writing joins.
7. **automation**: GitHub Actions cron (weekly): crawl -> diff manifest -> parse only new/changed -> validate -> publish
   to static hosting (GitHub Pages; decided - data expected well under the ~1 GB site cap, revisit if it grows). A run with zero changes commits nothing.

## Local runs and AI triggers
- Everything the CI cron does is runnable locally: `make update` (crawl -> download -> parse -> validate -> publish), each step idempotent
  and resumable; `make test`. The GitHub Actions workflow just calls the same targets.
- AI triggers live in `prompts/` (templates + trigger table in `prompts/README.md`), runnable headless via `make ai-<task>`
  (wraps `claude -p` with scoped `--allowedTools`) or interactively as `/<task>` slash commands in `.claude/commands/`.
  Tasks: fix-parser (quarantined files), extract-pdf, onboard-section, describe-catalog, review-conflicts, nl2sql-eval.
- Pipeline emits a `data/quarantine/` + `.error.json` for anything it cannot parse/validate and prints the exact `make ai-...`
  command to run next. **CI never runs AI tasks** (decided): a failed run opens an issue listing the exact `make ai-...` commands; a human runs them locally and reviews the diff.

## Decisions
- Hosting: GitHub Pages. Charts: Observable Plot. Spec->SQL compiler: Python reference + JS port sharing test vectors.
- CI never runs AI tasks. Local AI via `claude -p` (see prompts/).

## Milestones
- M0 Scaffold + dry-run parser as test fixture (done).
- M1 Crawler + manifest + dedupe for the air-transport statistics section; coverage-by-year report; idempotent re-runs.
- M2 Parse all xlsx in that section (both layouts) with validation; Parquet + catalog publish.
- M2.5 Semantic model (facts/dimensions/measures/joins) + spec->SQL compiler with tests (Python reference + JS port, shared test vectors).
- M3 Static site: dashboard builder (field picker, filters, chart types, cross-filter, save/share), curated dashboards, downloads; SQL as advanced drawer.
- M4 AI query tab (Chrome built-in AI + WebLLM, setup instructions) + eval set of ~30 questions (NL -> expected chart spec / result); LLM build-time extraction for PDFs.
- M5 CI cron + deploy; expand to other Data & Reports sections one at a time.

## Principles
- Provenance on every row (source URL, file hash, parser). Never silently coerce: unparseable -> quarantine table.
- Parsers are tested against saved fixtures in `tests/fixtures/`.
- Respect the site: low concurrency, identify the client, cache everything.
- Open questions: licence/terms of reuse for DGCA data; data size budget per release.

## Status (2026-10-03) - what exists
- **Done, tested end to end**: crawl -> manifest (dedupe by URL/ETag/sha256) -> parse (2 layouts + validators + quarantine) -> publish
  (merged series Apr 2014 - Aug 2026, 32 operators) -> semantic model -> spec compiler (Python + JS, shared vectors) ->
  DuckDB-WASM dashboard builder -> Ask-AI panel (Chrome Prompt API + WebLLM, tested with stubs) -> whole-site data catalog.
- **Whole-site crawl**: 16,058 portal nodes, 13.8k file links (9.6k pdf, 4.4k spreadsheets), 1,111 extracted HTML tables. Route kinds
  handled: static pages, rule-book JSON, `name/rb/ct/html`, dynamic applications, `viewDynamicRule*`, token attachments (`dynamicPdf/`).
  Unresolved (24) are dead or mistyped links on dgca.gov.in itself.
- **City-pair traffic done** (Apr 2015 - Aug 2026, 133k directed rows, 147 places, 1.6k routes): own parser (3 layouts, footer-validated),
  curated place harmonisation, role-playing joins in the compiler, cross-checked against airline totals (125/134 months within 1%; the 9
  outliers are a documented missing Jet Airways file).
- **Quarterly international traffic done** (Q1 2015 - Q2 2026; 46 quarters x 4 tables): carrier (Indian + 124 foreign), country and city-pair facts with a
  shared Direction dimension; every sheet validated against its own printed totals; footer misprints judged against an independent table of the same quarter;
  3-way table reconciliation (91/91) and an exact reconciliation with the monthly airline statistics (monthly = quarterly + Indian carriers' flights outside India,
  to the passenger, in every clean quarter).
- **Not yet in dashboards** (catalogued, downloadable on demand with `dgca download --section ... --ext ...`): fleet/personnel/financial tables, non-scheduled operators, pre-2014 years,
  accident/incident reports, licences, aerodromes. Each is an `onboard-section` AI task (see prompts/).

## Findings worth knowing
- The browser app has no runtime CDN dependency: `scripts/vendor.sh` bundles DuckDB-WASM (with apache-arrow), its Parquet *extension* (DuckDB-WASM otherwise
  downloads it from extensions.duckdb.org at runtime) and Observable Plot into gitignored `web/vendor` with pinned versions and checksums. Without it the app falls back to
  the CDN. WebLLM stays CDN-loaded on purpose (opt-in, large, and its models come from a third party anyway). CI/deploy must run `make vendor`.
- Portal is Hindi by default per session; `langChange('en_US')` switches it. Files live on public S3; dynamic documents are addressed by
  deterministic AES tokens fetched through `Upload?flag=iframeAttachView&attachId=`.
- Fiscal-year tables report capacity in millions, the newer calendar-year series in thousands; units are detected per column from headers.
- Final vs provisional: where the series overlap (Jan 2016 - Mar 2019) passenger counts match exactly; cargo/capacity differ for a few operators
  (Air India Express, Blue Dart) and are logged in `data/processed/conflicts.parquet`, final value wins.
- One source inconsistency kept in quarantine on purpose (`totaldom19`: capacity months do not sum to the published total).
- Scheduled international flights were suspended Mar 2020 - Mar 2022, so those months are real gaps (charts break the line).
- Different DGCA publications disagree in a few documented places (Q1 2022 monthly stats vs quarterly tables; Air India Express Nov-Dec 2022; IndiGo May 2024; Jet Airways Apr-Dec 2015 file missing). All are listed under `known_issues` in `semantic/semantic.yaml`, shown in the app's Data quality panel, and the publish checks fail on any *new* unexplained difference.
