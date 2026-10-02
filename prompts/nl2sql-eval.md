Task: maintain the NL->SQL eval set at tests/nl2sql/cases.yaml used to grade the in-browser models.
Add/refresh ~30 cases from web/data/catalog.json: question, gold SQL, expected result hash. Mix easy (filter), medium (group/aggregate),
hard (YoY, ratios, joins). Run gold SQL on the parquet to confirm it executes. Then run `make eval-nl2sql` and report the pass rate per
difficulty; if low, suggest changes to the prompt in web/src/ai/prompt.js (do not tune to the test set).
