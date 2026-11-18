# dgca-stats

Crawl, structure and query the aviation statistics published by India's DGCA (dgca.gov.in), and explore them in a
BI-style dashboard that runs **entirely in the browser**: no backend, no API keys. The site is static files
(Parquet + HTML/JS), queried client-side with DuckDB-WASM, so it can be hosted on GitHub Pages.

> Screenshots and a page-by-page tour: [docs/SITE.md](docs/SITE.md) · Design and decisions: [docs/PLAN.md](docs/PLAN.md)

## What it does

1. **Crawls** the DGCA portal (sitemap + every reachable page) and records every document in a manifest.
2. **Downloads** the statistics files (xlsx; pdf only when no xlsx twin exists), de-duplicated by URL, ETag and SHA-256.
3. **Parses** them with layout-specific deterministic parsers, each checked against the totals printed in the source.
   Anything that fails goes to `data/quarantine/` rather than being coerced.
4. **Publishes** versioned Parquet fact/dimension tables and a semantic catalog into `web/data/<version>/`.
5. **Serves** a static dashboard builder that turns a small JSON *chart spec* into DuckDB SQL and renders it.

## Datasets

| Fact table | Grain | Coverage |
|---|---|---|
| `airline_monthly` | airline x month x domestic/international: passengers, departures, hours, km, load factor, cargo | Apr 2014 - Aug 2026, 32 operators |
| `route_monthly` | directed city pair x month (domestic): passengers, freight, mail | Apr 2015 - Aug 2026, 133k rows, 147 places, 1.6k routes |
| `intl_airline_monthly` | carrier x month x direction: international passengers and freight (Indian and foreign carriers) | Jan 2015 onward, from the quarterly tables |
| `intl_country_quarterly` | country x carrier x direction x quarter | Q1 2015 - Q2 2026 (46 quarters) |
| `intl_citypair_quarterly` | international city pair x carrier x quarter | Q1 2015 - Q2 2026 |

Dimensions (airline, carrier, country, origin/destination place and city, route, fiscal year/quarter/month, segment, direction)
and measures (including derived ones such as passenger load factor, always recomputed from sums, never averaged) are
defined once in [`semantic/semantic.yaml`](semantic/semantic.yaml). Add an entry there and it appears in the builder.

Fiscal years run April-March. Scheduled international flights were suspended Mar 2020 - Mar 2022, so those months are real
gaps and charts break the line instead of drawing zeros. A few places where DGCA publications disagree with each other are
listed under `known_issues` in the semantic model and shown in the app's **Data quality** panel.

## How it works

```
dgca.gov.in ──crawl──▶ data/manifest.parquet ──download──▶ data/raw/ ──parse──▶ data/interim/ ──validate──▶ publish ──▶ web/data/<version>/*.parquet
                                                                │ failures                                          catalog.json (from semantic.yaml)
                                                                ▼                                                   latest.json (version pointer)
                                                        data/quarantine/ (+ .error.json)
```

- **Pipeline** (`src/dgca_stats/`): `crawl/` (discovery, whole-site crawl, downloads), `parse/` (monthly, city-pair,
  international-quarterly layouts and a registry), `publish/` (Parquet build, place/airline/country name harmonisation,
  cross-dataset checks), `validate.py`, `manifest.py`, `cli.py` (the `dgca` command). Every step is idempotent and resumable;
  re-runs only touch new or changed files. The client is rate-limited, retrying and identifies itself.
- **Provenance**: every row carries source URL, file hash and parser. Newer publications that restate older months win;
  superseded values and disagreements are logged to `data/processed/conflicts.parquet`.
- **Validation**: monthly sums must equal annual totals, quarterly sheets must match their own printed totals, city-pair
  traffic is cross-checked against airline totals, and international quarterly tables reconcile three ways and against
  the monthly statistics. The publish step fails on any *new* unexplained difference.
- **Spec compiler**: a chart spec `{fact, dimensions, measures, filters, sort, limit, viz}` is compiled to SQL against the
  semantic model. There is a Python reference (`src/dgca_stats/spec.py`) and a JS port (`web/src/spec.js`) that share test
  vectors, so every widget is introspectable ("View SQL") and testable without a browser.
- **Web app** (`web/`): DuckDB-WASM runs in a Web Worker and reads Parquet over HTTP range requests, so only the needed
  columns and row groups are downloaded. Charts use Observable Plot. Dashboards are saved in localStorage, exportable as JSON
  and shareable as URL-encoded specs.
- **Ask AI** (optional): a local model turns a plain-English question into a chart spec, which is validated against the
  semantic model before rendering. Tiers: Chrome built-in AI (Gemini Nano) or WebLLM on WebGPU (opt-in, ~1-2 GB download). With
  neither, the builder and starter dashboards work fully.
- **PWA**: web manifest, icons and a service worker (app shell cached, vendor files cache-first, data network-first). Install
  and offline need HTTPS or `localhost`.
- **No runtime CDN**: `scripts/vendor.sh` bundles DuckDB-WASM, its Parquet extension and Observable Plot into `web/vendor`
  (pinned, with checksums). Without it the app falls back to pinned CDN builds.

## Quick start

```bash
source ~/.venvs/cv-gpu/bin/activate
pip install -e . --no-deps && pip install playwright && playwright install chromium   # one-off
make vendor    # browser libraries into web/vendor (needs node/npm; gitignored)
make serve     # http://localhost:8765
```

`web/data/` is produced by the pipeline (it is gitignored by the top-level `data/` rule, see "Hosting" below). To rebuild it:

```bash
make update    # crawl -> download -> parse -> validate -> publish
```

Serve on your network: `python scripts/serve.py 8765 0.0.0.0` (static files from `web/` only, no authentication).

## Commands

| Command | Purpose |
|---|---|
| `make update` | air-traffic statistics: crawl, download, parse, validate, publish |
| `make update-all` | the above plus the whole-portal crawl |
| `make crawl` / `download` / `parse` / `validate` / `publish` | individual steps (`dgca <step>`; `--years`, `--section`, `--ext`, `--force` where relevant) |
| `make site-crawl` / `site-report` | crawl and summarise the whole portal (16k nodes, 13.8k file links, 1.1k extracted HTML tables) |
| `make vendor` | fetch DuckDB-WASM, Parquet extension and Observable Plot into `web/vendor` |
| `make serve` | static server with HTTP Range support, like GitHub Pages |
| `make test` | Python tests (205) |
| `make test-web` | JS compiler vectors + Playwright e2e; regenerates `docs/screenshots/` |
| `make ai-<task>` | headless Claude Code tasks (below) |

## AI-assisted maintenance

Prompt templates in `prompts/` run headless through `claude -p` with scoped tools: `fix-parser` (a file landed in quarantine),
`extract-pdf`, `onboard-section` (a new portal section), `describe-catalog`, `review-conflicts`, `nl2sql-eval`. The pipeline prints
the exact `make ai-...` command to run next when something is quarantined. CI never runs AI tasks; a human reviews the diff.
See [prompts/README.md](prompts/README.md).

## Layout

```
src/dgca_stats/   crawl, parse, publish, validate, CLI          semantic/semantic.yaml   facts, dimensions, measures, known issues
web/              static site (index.html, src/*.js, sw.js)      prompts/                 AI task templates
web/data/         published Parquet + catalog (build output)     scripts/                 serve.py, vendor.sh, make_icons.py
web/vendor/       bundled browser libs (gitignored)              tests/                   pytest, fixtures, vectors, Playwright e2e
data/             raw files, manifest, interim (gitignored)      docs/                    PLAN.md, SITE.md, screenshots/
```

## Hosting

The site is a folder of static files, so any static host works; GitHub Pages is the intended one. See
[docs/SITE.md](docs/SITE.md#deploying-to-github-pages) for the setup.

## Data and licensing

All data comes from public DGCA documents; keep the source links. Check DGCA's terms before redistributing the data.
