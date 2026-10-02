Task: extract tables from a PDF that has no xlsx equivalent.
Input: $FILE and its manifest row (section, year, entity, table_id).
Steps:
1. Read docs/PLAN.md and the target schema for this table_id in src/dgca_stats/parse/schemas/ (create it from a sibling table if missing).
2. Extract with pdfplumber first; only fall back to reading the page visually where text extraction is wrong.
3. Write tidy rows to data/interim/<table_id>/<file_sha256>.parquet using the schema exactly, with columns
   source_url, file_sha256, parsed_by='llm'. Do not invent values; use null for unreadable cells.
4. Run `dgca validate --only <that file>`. Totals must reconcile; otherwise write the file to data/quarantine/ with a reason.
Report: rows extracted, cells left null, validation result.
