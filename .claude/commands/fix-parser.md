---
description: DGCA AI task fix-parser (see prompts/README.md)
argument-hint: file or section
---

Task: a DGCA file failed parsing or validation and was quarantined.
Input: $ARGUMENTS (plus its sidecar `.error.json` with the traceback / failed checks, and its manifest row in data/manifest.parquet).
Steps:
1. Read docs/PLAN.md and the nearest existing parser in src/dgca_stats/parse/.
2. Open the file, work out how its layout differs (headers, month labels, merged cells, units, legacy-font titles).
3. Extend or add a parser (prefer a small variant of an existing one, registered by section + layout). Do not special-case one file.
4. Copy the file to tests/fixtures/ (if < 200 KB) and add a test asserting the validation checks (e.g. monthly sums == annual total).
5. Run `pytest` and `dgca parse --only $ARGUMENTS`; the file must leave quarantine and pass `dgca validate`.
Report: what differed, what you changed, and any rows you could not reconcile. If a checksum still fails, stop and say so.
