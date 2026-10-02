# dgca-stats
Crawl, structure and query DGCA India aviation statistics. Everything the viewer does runs in the browser (DuckDB-WASM).
See `docs/PLAN.md` for the design and `docs/screenshots/` for the UI.

```
source ~/.venvs/cv-gpu/bin/activate
pip install -e . --no-deps && pip install playwright && playwright install chromium   # one-off
make update          # air-traffic statistics: crawl -> download -> parse -> validate -> publish (idempotent)
make site-crawl      # whole portal: sitemap + every reachable page; records files and extracted HTML tables
make site-report     # what the site crawl found
make publish         # (or `dgca publish`) rebuild web/data from the manifest
make serve           # http://localhost:8765  (static server with HTTP Range support, like GitHub Pages)
make test            # python unit tests;  make test-web  # JS compiler vectors + Playwright e2e (+screenshots)
```
Layout: `src/dgca_stats/{crawl,parse,llm,publish}`, `semantic/semantic.yaml` (facts, dimensions, measures),
`web/` (static site), `data/` (gitignored: raw files, manifest, interim), `prompts/` (AI task templates), `tests/`.
