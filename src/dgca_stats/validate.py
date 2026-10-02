"""Cross-run checks and coverage reporting."""
import pandas as pd

from .parse.registry import dataset_for


def coverage(df: pd.DataFrame) -> str:
    known = df[df.apply(lambda r: dataset_for(r["title"], r["ext"]) is not None, axis=1)]
    if known.empty:
        return "no parseable files in manifest"
    t = known.pivot_table(index="fy", columns="parse_status", values="s3_url", aggfunc="count", fill_value=0)
    t["files"] = t.sum(axis=1)
    miss = known[known.status == "missing"].groupby("fy").size().rename("missing_at_source")
    return t.join(miss).fillna(0).astype(int).to_string()


def report(df: pd.DataFrame) -> int:
    print(coverage(df))
    known = df[df.apply(lambda r: dataset_for(r["title"], r["ext"]) is not None, axis=1)]
    bad = known[known.parse_status == "quarantined"]
    pending = known[(known.status == "downloaded") & (known.parse_status == "pending")]
    for _, r in bad.iterrows():
        print(f"QUARANTINED {r.fy} {r.entity} {r.table_id}: {r.error}\n  -> make ai-fix-parser FILE=data/quarantine/{r.content_sha256}.{r.ext}")
    if len(pending):
        print(f"{len(pending)} downloaded files not parsed yet (run `dgca parse`)")
    return 1 if len(bad) else 0
