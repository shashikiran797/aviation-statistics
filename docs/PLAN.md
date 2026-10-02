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

## Architecture
1. **crawl**: discover pages -> `data/manifest.parquet` (url, s3_url, section, year, entity, table_id, ext, sha256, status).
   Polite: rate limit, retry, cache raw files in `data/raw/` keyed by URL, resumable.
2. **parse**: per-format parsers registered by (section, layout). Deterministic first (xlsx/html tables), validated
   with checksums. Output tidy long tables in `data/interim/`.
3. **llm**: fallback only for PDFs / unknown layouts / header normalisation (Claude, structured output into a
   fixed schema); results cached by file hash, always flagged `parsed_by=llm` and validated the same way.
4. **db**: load into DuckDB (`data/processed/dgca.duckdb`) with a documented schema + data dictionary.
5. **app**: Streamlit. Tabs: Explore (filters + charts), Table browser/download, Ask (NL -> SQL via Claude,
   read-only connection, shows the SQL and the source file for every answer).

## Milestones
- M0 Scaffold + dry-run parser as test fixture (done).
- M1 Crawler + manifest for the air-transport statistics section; report coverage by year.
- M2 Parse all xlsx in that section (all 2 layouts) with validation report; DuckDB load.
- M3 Streamlit Explore UI on that data.
- M4 LLM extraction for PDFs/zip-era years; NL->SQL "Ask" tab with eval set of ~30 questions.
- M5 Expand to other Data & Reports sections (fleet, aerodromes, licences, safety), one section at a time.
- M6 Scheduled refresh (new files detected by manifest diff), deploy.

## Principles
- Provenance on every row (source URL, file hash, parser). Never silently coerce: unparseable -> quarantine table.
- Parsers are tested against saved fixtures in `tests/fixtures/`.
- Respect the site: low concurrency, identify the client, cache everything.
- Open questions: licence/terms of reuse for DGCA data; hosting target for the public UI.
