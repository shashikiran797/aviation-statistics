# dgca-stats
Crawl, structure and query DGCA India aviation statistics. See `docs/PLAN.md`.

```
source ~/.venvs/cv-gpu/bin/activate
pip install -e . --no-deps
pytest
```
Layout: `src/dgca_stats/{crawl,parse,llm,db,app}`, `data/` (gitignored), `scripts/` (one-off), `tests/`.
