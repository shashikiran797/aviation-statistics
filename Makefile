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
update: crawl download parse validate publish   # the same chain CI runs
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
