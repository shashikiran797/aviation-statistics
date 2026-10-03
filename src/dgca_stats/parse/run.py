"""Drive parsing from the manifest: parse pending files, write interim parquet, quarantine failures."""
import json
import shutil

import pandas as pd

from .. import config
import re

from . import city_pair, intl_quarterly, monthly, monthly_cal
from .registry import dataset_for, operator_from_title


def _sibling(df, row, table: int):
    """Manifest row of another table of the same quarter (same file stem, different _N suffix), if downloaded."""
    stem = re.sub(r"_[1-4]\.xlsx$", f"_{table}.xlsx", str(row["portal_path"]), flags=re.I)
    hit = df[(df["portal_path"] == stem) & (df["status"] == "downloaded")]
    return None if hit.empty else hit.iloc[0]


def _parse_intl(df, i, row, ds, path, stats):
    kind = {1: "airline", 2: "airline_month", 3: "country", 4: "citypair"}[ds["table"]]
    year, q = ds["year"], ds["quarter"]
    if kind == "citypair":
        p = intl_quarterly.citypair(path)
        ref = None
        for t in (1, 3):                                   # independent table of the same quarter to referee the footers
            sib = _sibling(df, row, t)
            if sib is not None:
                ref = intl_quarterly.read_named(sib["raw_path"] if str(sib["raw_path"]).startswith("/") else config.ROOT / sib["raw_path"],
                                                "airline" if t == 1 else "country")["totals"].get("all")
                if ref:
                    break
        problems = intl_quarterly.validate_citypair(p, ref)
        tidy = intl_quarterly.citypair_long(p, year, q)
        sub = "intl_citypair_quarterly"
    else:
        p = intl_quarterly.read_named(path, kind)
        problems = intl_quarterly.validate_named(p)
        tidy = intl_quarterly.named_long(p, year, q)
        sub = {"airline": "intl_airline_quarterly", "airline_month": "intl_airline_monthly", "country": "intl_country_quarterly"}[kind]
    tq = intl_quarterly.title_quarter(p["title"]) if kind != "citypair" else None
    if tq and tq != (year, q):
        problems.append(f"title says {tq} but file name says {(year, q)}")
    hard = [x for x in problems if not x.startswith("warn:")]
    if hard:
        raise monthly.LayoutError("; ".join(hard))
    notes = [x for x in problems if x.startswith("warn:") and "no SUB TOTAL" not in x and "no GRAND TOTAL" not in x]
    if p.get("footnoted"):
        notes.append(f"warn: {p['footnoted']} cells carry a footnote asterisk (kept)")
    tidy = tidy.assign(provisional="(P)" in p.get("title", "").upper() if kind != "citypair" else True, source_url=row["s3_url"], file_sha256=row["content_sha256"], parsed_by="code")
    d2 = config.INTERIM / sub
    d2.mkdir(parents=True, exist_ok=True)
    tidy.to_parquet(d2 / f"{row['content_sha256']}.parquet", index=False)
    df.at[i, "parse_status"], df.at[i, "parse_rows"], df.at[i, "error"] = "ok", len(tidy), ("; ".join(notes)[:300] if notes else None)
    stats["ok"] += 1
    stats["warnings"] += len(notes)


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
            if ds["dataset"] == "intl_quarterly":
                _parse_intl(df, i, row, ds, path, stats)
                continue
            if ds["dataset"] == "city_pair_monthly":
                fn = str(row["portal_path"]).rsplit("/", 1)[-1]
                parsed = city_pair.read(path, fn)
                problems = city_pair.validate(parsed)
                hard = [x for x in problems if not x.startswith("warn:")]
                stats["warnings"] += len(problems) - len(hard)
                if hard:
                    raise monthly.LayoutError("; ".join(hard))
                period = parsed["title_period"] if parsed["title_period"] is not None else parsed["file_period"]
                tidy = city_pair.directional(parsed, period).assign(source_url=row["s3_url"], file_sha256=row["content_sha256"], parsed_by="code")
                d2 = config.INTERIM / "city_pair"
                d2.mkdir(parents=True, exist_ok=True)
                tidy.to_parquet(d2 / f"{row['content_sha256']}.parquet", index=False)
                df.at[i, "parse_status"], df.at[i, "parse_rows"], df.at[i, "error"] = "ok", len(tidy), None
                stats["ok"] += 1
                continue
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
