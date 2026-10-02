# AI triggers

Each task is a prompt template here, runnable two ways:
- Headless: `make ai-<task> ARGS="..."` (wraps `claude -p`, see Makefile). Used locally and in CI.
- Interactive: `/<task>` slash command in Claude Code (`.claude/commands/<task>.md`, same instructions).

Ground rules baked into every prompt: read `docs/PLAN.md` first; never edit `data/raw`; every output carries
provenance (`source_url`, `file_sha256`, `parsed_by=llm`); output must pass `dgca validate`; add a fixture + test
for any new parser; stop and report instead of guessing when a checksum fails.

| Task | Trigger (automatic) | Command |
|---|---|---|
| fix-parser | a file lands in `data/quarantine/` (parse/validation failure) | `make ai-fix-parser FILE=data/quarantine/<name>` |
| extract-pdf | manifest row has a PDF with no xlsx twin | `make ai-extract-pdf FILE=data/raw/<name>.pdf` |
| onboard-section | crawl finds a section with no registered layout | `make ai-onboard-section SECTION=<manifest section>` |
| describe-catalog | new/changed fact table after publish | `make ai-describe-catalog` |
| review-conflicts | `conflicts.parquet` grew after a run | `make ai-review-conflicts` |
| nl2sql-eval | catalog changed or before release | `make ai-nl2sql-eval` |
