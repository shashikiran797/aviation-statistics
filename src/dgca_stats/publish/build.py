"""Build the static data bundle: dimension + fact parquet, conflicts, provenance, semantic catalog."""
import hashlib
import json
import shutil

import pandas as pd
import yaml

from .. import config
from ..parse.registry import dataset_for
from .names import AGGREGATES, canonical, display, slug

FACT_COLS = ["departures", "hours", "kms_thousand", "pax", "pax_km_million", "ask_million",
             "freight_t", "mail_t", "cargo_t", "tkm_total_million", "atk_million"]
MONTH_NAMES = {"APR": "Apr", "MAY": "May", "JUN": "Jun", "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct",
               "NOV": "Nov", "DEC": "Dec", "JAN": "Jan", "FEB": "Feb", "MAR": "Mar"}
FY_ORDER = list(MONTH_NAMES)  # April-first


def load_parsed(man: pd.DataFrame) -> pd.DataFrame:
    ok = man[(man.parse_status == "ok") & man.apply(lambda r: dataset_for(r["title"], r["ext"]) is not None, axis=1)]
    frames = []
    for _, r in ok.iterrows():
        f = config.INTERIM / "airline_monthly" / f"{r.content_sha256}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            d["first_seen"], d["last_modified"] = r.first_seen, r.last_modified
            frames.append(d)
    if not frames:
        raise SystemExit("nothing parsed yet: run `dgca parse`")
    return pd.concat(frames, ignore_index=True)


def dedupe(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Row-level dedupe on the natural key. Newest-published file wins; value disagreements are logged."""
    key = ["airline_key", "fy", "segment", "month"]
    d = d.sort_values(["first_seen", "file_sha256"], kind="stable")
    dup = d[d.duplicated(key, keep=False)]
    conflicts = []
    for k, g in dup.groupby(key):
        vals = g[FACT_COLS].round(6).drop_duplicates()
        if len(vals) > 1:
            conflicts.append(g.assign(resolution="kept newest"))
    out = d.drop_duplicates(key, keep="last")
    return out, (pd.concat(conflicts) if conflicts else pd.DataFrame(columns=list(d.columns) + ["resolution"]))


def build(man: pd.DataFrame) -> dict:
    d = load_parsed(man)
    d["entity_key"] = d["entity"].map(canonical)
    d["airline_key"] = d["entity_key"].map(slug)
    n_in = len(d)
    d, conflicts = dedupe(d)
    n_dupes = n_in - len(d)

    # --- dimensions
    ents = d.groupby(["airline_key", "entity_key"]).fy.agg(["min", "max"]).reset_index()
    dim_airline = pd.DataFrame({
        "airline_key": ents.airline_key,
        "airline": ents.entity_key.map(display),
        "airline_type": ents.entity_key.map(lambda k: "Aggregate" if k in AGGREGATES else "Airline"),
        "first_fy": ents["min"], "last_fy": ents["max"],
    }).sort_values(["airline_type", "airline"]).reset_index(drop=True)

    periods = d[["fy", "month"]].drop_duplicates()
    rows = []
    for fy in sorted(periods.fy.unique()):
        y0 = int(fy[:4])
        for i, m in enumerate(FY_ORDER, 1):
            cal_year = y0 if i <= 9 else y0 + 1
            cal_month = (i + 2) % 12 + 1
            rows.append({"period_key": cal_year * 100 + cal_month, "period": pd.Timestamp(cal_year, cal_month, 1).date(),
                         "fy": fy, "fy_start_year": y0, "quarter": f"{fy} Q{(i - 1) // 3 + 1}", "month_name": MONTH_NAMES[m],
                         "fy_month_num": i, "calendar_year": cal_year, "_m": m})
    dim_period = pd.DataFrame(rows)
    dim_segment = pd.DataFrame({"segment_key": [1, 2], "segment": ["Domestic", "International"]})

    # --- fact
    seg_key = {"domestic": 1, "international": 2}
    pk = dim_period.set_index(["fy", "_m"]).period_key
    fact = pd.DataFrame({
        "airline_key": d.airline_key.values,
        "period_key": [pk[(fy, m)] for fy, m in zip(d.fy, d.month)],
        "segment_key": d.segment.map(seg_key).values,
        **{c: d[c].values for c in FACT_COLS},
        "source_sha": d.file_sha256.str[:12].values,
    }).sort_values(["period_key", "airline_key", "segment_key"]).reset_index(drop=True)
    dim_period = dim_period.drop(columns="_m")
    present = set(fact.period_key)
    dim_period = dim_period[dim_period.period_key.isin(present)].reset_index(drop=True)

    version = hashlib.sha256(pd.util.hash_pandas_object(fact, index=False).values.tobytes()).hexdigest()[:10]
    out = config.SITE_DATA / version
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tables = {"fact_airline_monthly": fact, "dim_airline": dim_airline, "dim_period": dim_period, "dim_segment": dim_segment}
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False, compression="zstd", row_group_size=50_000)

    # --- semantic catalog (validated against what we actually wrote)
    catalog = make_catalog(tables, version)
    (out / "catalog.json").write_text(json.dumps(catalog, indent=1))

    # --- provenance + conflicts
    src = man[man.parse_status == "ok"][["content_sha256", "s3_url", "fy", "entity", "table_id", "last_modified"]].copy()
    src["sha12"] = src.content_sha256.str[:12]
    (out / "sources.json").write_text(src.drop(columns="content_sha256").to_json(orient="records"))
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    conflicts.to_parquet(config.PROCESSED / "conflicts.parquet", index=False)

    (config.SITE_DATA / "latest.json").write_text(json.dumps({"version": version, "built_at": pd.Timestamp.now("UTC").isoformat()}))
    for old in config.SITE_DATA.iterdir():
        if old.is_dir() and old.name != version:
            shutil.rmtree(old)
    print(f"publish: version {version}: {len(fact)} fact rows, {len(dim_airline)} airlines, "
          f"{len(dim_period)} months; {n_dupes} duplicate rows dropped, {len(conflicts)} conflicting rows logged")
    return {"version": version, "rows": len(fact)}


def make_catalog(tables: dict, version: str) -> dict:
    model = yaml.safe_load((config.ROOT / "semantic" / "semantic.yaml").read_text())
    errors = []
    for fid, f in model["facts"].items():
        if f["table"] not in tables:
            errors.append(f"fact {fid}: table {f['table']} not published")
            continue
        cols = set(tables[f["table"]].columns)
        for alias, (fk, dt, dk) in f["joins"].items():
            if fk not in cols or dk not in tables[dt].columns:
                errors.append(f"fact {fid}: bad join {alias}")
        for d in f["dimensions"]:
            dm = model["dimensions"][d]
            if dm["column"] not in tables[dm["table"]].columns:
                errors.append(f"dimension {d}: column {dm['column']} missing in {dm['table']}")
            if dm.get("sort_by") and dm["sort_by"] not in tables[dm["table"]].columns:
                errors.append(f"dimension {d}: sort_by {dm['sort_by']} missing")
        for m in f["measures"]:
            for c in __import__("re").findall(r"f\.(\w+)", model["measures"][m]["expr"]):
                if c not in cols:
                    errors.append(f"measure {m}: fact column {c} missing")
    if errors:
        raise SystemExit("semantic model invalid:\n  " + "\n  ".join(errors))
    for did, dm in model["dimensions"].items():
        t = tables[dm["table"]]
        if dm["kind"] == "category":
            sort = dm.get("sort_by") or dm["column"]
            vals = t.sort_values(sort)[dm["column"]].drop_duplicates().tolist()
            dm["values"] = vals
        else:
            dm["min"], dm["max"] = str(t[dm["column"]].min()), str(t[dm["column"]].max())
    model["version"] = version
    model["tables"] = {n: list(df.columns) for n, df in tables.items()}
    return model
