PY ?= ~/.venvs/cv-gpu/bin/python
CLAUDE ?= claude
TOOLS ?= Read,Write,Edit,Glob,Grep,Bash(pytest:*),Bash(dgca:*),Bash(python:*)
RULES := Follow prompts/README.md ground rules. Read docs/PLAN.md first.

# --- local scraping pipeline (all idempotent; re-runs only touch new/changed files) ---
crawl:      ; $(PY) -m dgca_stats.cli crawl
download:   ; $(PY) -m dgca_stats.cli download
parse:      ; $(PY) -m dgca_stats.cli parse
validate:   ; $(PY) -m dgca_stats.cli validate
publish:    ; $(PY) -m dgca_stats.cli publish
site-crawl: ; $(PY) -m dgca_stats.cli site-crawl
site-report: ; $(PY) -m dgca_stats.cli site-report
update: crawl download parse validate publish   # the same chain CI runs (stats section)
update-all: crawl site-crawl download parse validate publish
vendor: ; ./scripts/vendor.sh   # DuckDB-WASM + Parquet extension + chart lib into web/vendor (no CDN needed)
web/vendor/MANIFEST.json: ; ./scripts/vendor.sh
serve: web/vendor/MANIFEST.json ; $(PY) scripts/serve.py 8765
test-web: web/vendor/MANIFEST.json ; node --test web/test/spec.test.mjs && ~/.venvs/cv-gpu/bin/pytest tests/e2e -q
test:       ; ~/.venvs/cv-gpu/bin/pytest -q

# --- AI triggers: headless Claude Code, prompt template + args ---
define ai
	$(CLAUDE) -p "$$(FILE='$(FILE)' SECTION='$(SECTION)' envsubst < prompts/$(1).md)" --allowedTools "$(TOOLS)" --append-system-prompt "$(RULES)"
endef
ai-fix-parser:       ; $(call ai,fix-parser)
ai-extract-pdf:      ; $(call ai,extract-pdf)
ai-onboard-section:  ; $(call ai,onboard-section)
ai-describe-catalog: ; $(call ai,describe-catalog)
ai-review-conflicts: ; $(call ai,review-conflicts)
ai-nl2sql-eval:      ; $(call ai,nl2sql-eval)
# Run every pending AI fix (quarantined files), e.g. after `make update`
ai-fix-all: ; for f in data/quarantine/*; do case $$f in *.error.json) ;; *) $(MAKE) ai-fix-parser FILE=$$f;; esac; done
