"""Drive parsing from the manifest: parse pending files, write interim parquet, quarantine failures."""
import json
import shutil

import pandas as pd

from .. import config
import re

from . import monthly, monthly_cal
from .registry import dataset_for, operator_from_title


def run(df: pd.DataFrame, only_pending=True, log=print) -> dict:
    stats = {"ok": 0, "quarantined": 0, "skipped": 0, "warnings": 0}
    out_dir = config.INTERIM / "airline_monthly"
    out_dir.mkdir(parents=True, exist_ok=True)
    config.QUARANTINE.mkdir(parents=True, exist_ok=True)
    for i in df.index:
        row = df.loc[i]
        if row["status"] != "downloaded":
            continue
        ds = dataset_for(row["title"], row["ext"], row["portal_path"])
        if ds is None:
            continue
        if only_pending and row["parse_status"] == "ok":
            continue
        if isinstance(row["duplicate_of"], str):  # same bytes already parsed under another URL
            df.at[i, "parse_status"], df.at[i, "parse_rows"] = "skipped", 0
            stats["skipped"] += 1
            continue
        path = config.ROOT / row["raw_path"] if not str(row["raw_path"]).startswith("/") else row["raw_path"]
        try:
            if ds["dataset"] == "airline_monthly_cal":
                fn = str(row["portal_path"]).rsplit("/", 1)[-1]
                ym = re.search(r"(\d{2})\.xlsx$", fn, re.I)
                rows, problems = monthly_cal.read(path, 2000 + int(ym[1]) if ym else None)
                if problems:
                    raise monthly.LayoutError("; ".join(problems))
                tidy = rows.assign(entity=operator_from_title(row["title"]), source_url=row["s3_url"], file_sha256=row["content_sha256"], parsed_by="code")
                tidy.to_parquet(out_dir / f"{row['content_sha256']}.parquet", index=False)
                df.at[i, "parse_status"], df.at[i, "parse_rows"], df.at[i, "error"] = "ok", len(tidy), None
                stats["ok"] += 1
                continue
            months, annual = monthly.read(path, row["fy"])
            problems = monthly.validate(months, annual)
            hard = [p for p in problems if not p.startswith("warn:")]
            stats["warnings"] += len(problems) - len(hard)
            if hard:
                raise monthly.LayoutError("; ".join(hard))
            tidy = monthly.tidy(months, row["entity"], row["fy"], ds["segment"],
                                {"source_url": row["s3_url"], "file_sha256": row["content_sha256"], "parsed_by": "code"})
            tidy.to_parquet(out_dir / f"{row['content_sha256']}.parquet", index=False)
            df.at[i, "parse_status"], df.at[i, "parse_rows"], df.at[i, "error"] = "ok", len(tidy), None
            stats["ok"] += 1
        except Exception as e:  # any failure -> quarantine, never silently drop
            dest = config.QUARANTINE / f"{row['content_sha256']}.{row['ext']}"
            shutil.copy(path, dest)
            dest.with_suffix(dest.suffix + ".error.json").write_text(json.dumps(
                {"error": str(e), "s3_url": row["s3_url"], "fy": row["fy"], "entity": row["entity"], "table_id": row["table_id"]}, indent=2, default=str))
            df.at[i, "parse_status"], df.at[i, "error"] = "quarantined", str(e)[:300]
            stats["quarantined"] += 1
    log(f"parse: {stats}")
    return stats
