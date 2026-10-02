"""The manifest: one row per discovered file. Single source of truth for crawl state."""
from datetime import datetime, timezone

import pandas as pd

from . import config

COLUMNS = {
    "s3_url": "string", "portal_path": "string", "section": "string", "fy": "string", "entity": "string",
    "table_id": "string", "title": "string", "ext": "string", "source_page": "string",
    "first_seen": "string", "last_seen": "string",
    "status": "string",            # discovered | downloaded | missing | error
    "http_status": "Int64", "etag": "string", "last_modified": "string", "size": "Int64",
    "content_sha256": "string", "duplicate_of": "string", "raw_path": "string",
    "parse_status": "string",      # pending | ok | quarantined | skipped
    "parse_rows": "Int64", "error": "string",
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in COLUMNS.items()})


def load(path=None) -> pd.DataFrame:
    path = path or config.MANIFEST
    if not path.exists():
        return empty()
    return pd.read_parquet(path).astype(COLUMNS)


def save(df: pd.DataFrame, path=None) -> None:
    path = path or config.MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(["fy", "entity", "table_id", "ext"], kind="stable").reset_index(drop=True).to_parquet(path, index=False)


def upsert(df: pd.DataFrame, found: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Merge freshly discovered rows into the manifest. Never loses download/parse state of known rows."""
    ts = now()
    idx = {u: i for i, u in enumerate(df["s3_url"])}
    new_rows, seen = [], 0
    for rec in found:
        i = idx.get(rec["s3_url"])
        if i is None:
            row = {c: None for c in COLUMNS}
            row.update(rec, first_seen=ts, last_seen=ts, status="discovered", parse_status="pending")
            new_rows.append(row)
            idx[rec["s3_url"]] = -1
        elif i >= 0:
            df.loc[df.index[i], "last_seen"] = ts
            if str(df.at[df.index[i], "section"]).startswith("site/") and str(rec.get("section", "")).startswith("site/"):
                # labels improve as the crawler gets smarter; never touch rows owned by a registered dataset section
                for k in ("title", "entity", "fy"):
                    if rec.get(k):
                        df.loc[df.index[i], k] = rec[k]
            seen += 1
    if new_rows:
        add = pd.DataFrame(new_rows).astype(COLUMNS)
        df = add if df.empty else pd.concat([df, add], ignore_index=True)
    return df, {"new": len(new_rows), "seen_again": seen}
