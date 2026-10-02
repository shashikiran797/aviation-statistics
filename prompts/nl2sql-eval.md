Task: maintain the NL->chart-spec eval set at tests/ai/cases.yaml used to grade the in-browser models.
Add/refresh ~30 cases from web/data/catalog.json: question, gold chart spec, expected result hash (gold spec compiled and run
on the parquet). Mix easy (single measure + filter), medium (group by + time series), hard (YoY, ratio, top-N, multi-fact).
Run `make eval-ai` and report pass rate per difficulty and per backend; if low, suggest changes to the prompt in
web/src/ai/prompt.js (do not tune to the test set).
