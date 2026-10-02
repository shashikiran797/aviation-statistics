---
description: DGCA AI task review-conflicts (see prompts/README.md)
argument-hint: file or section
---

Task: review data/processed/conflicts.parquet (rows where a newer report disagrees with an earlier value for the same natural key).
Group by cause (restatement, units change, parse error, typo at source). For each group give a recommendation: accept newer,
keep older, or fix parser. Write docs/conflicts/<date>.md. Only change code if the cause is a parser bug, with a test.
