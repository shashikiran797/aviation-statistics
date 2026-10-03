"""Build the static data bundle: dimension + fact parquet, conflicts, provenance, semantic catalog."""
import hashlib
import json
import shutil

import pandas as pd
import yaml

from .. import config
from ..parse.registry import dataset_for
from . import checks as checks_mod
from . import intl as intl_pub
from . import places
from . import site as site_pub
from .names import AGGREGATES, canonical, display, slug

FACT_COLS = ["departures", "hours", "kms_thousand", "pax", "pax_km_million", "ask_million",
             "freight_t", "mail_t", "cargo_t", "tkm_total_million", "atk_million"]
_UNUSED_MONTH_NAMES = {"APR": "Apr", "MAY": "May", "JUN": "Jun", "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct",
               "NOV": "Nov", "DEC": "Dec", "JAN": "Jan", "FEB": "Feb", "MAR": "Mar"}


def load_parsed(man: pd.DataFrame) -> pd.DataFrame:
    ok = man[(man.parse_status == "ok") & man.apply(lambda r: dataset_for(r["title"], r["ext"], r["portal_path"]) is not None, axis=1)]
    frames = []
    for _, r in ok.iterrows():
        f = config.INTERIM / "airline_monthly" / f"{r.content_sha256}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            if d.empty:
                continue
            if "period" not in d.columns:                       # fiscal-year tables: APR..MAR within a fiscal year
                y0 = d.fy.str[:4].astype(int)
                mon = d.month.map({m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)})
                d["period"] = pd.to_datetime(dict(year=y0 + (mon < 4).astype(int), month=mon, day=1))
                d["provisional"] = False
            d["period"] = pd.to_datetime(d["period"])
            d["first_seen"], d["last_modified"] = r.first_seen, r.last_modified
            frames.append(d[["entity", "segment", "period", "provisional", *FACT_COLS, "file_sha256", "first_seen", "last_modified"]])
    if not frames:
        raise SystemExit("nothing parsed yet: run `dgca parse`")
    return pd.concat(frames, ignore_index=True)


def dedupe(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Row-level dedupe on the natural key (airline, month, segment).

    The fiscal-year tables (final) and the calendar-year series (often provisional) overlap on Jan 2015 - Mar 2019.
    Precedence: final over provisional, then most recently published. Real disagreements are logged, not hidden."""
    key = ["airline_key", "period", "segment"]
    d = d.assign(_prov=d.provisional.astype(int)).sort_values(["_prov", "first_seen", "file_sha256"], ascending=[True, False, True], kind="stable")  # final first, then newest
    conflicts = []
    for _, g in d[d.duplicated(key, keep=False)].groupby(key):
        a = g[FACT_COLS].astype(float)
        spread = (a.max() - a.min()).abs()
        scale = a.abs().max().clip(lower=1)
        if ((spread > 0.5) & (spread / scale > 1e-3)).any():
            conflicts.append(g.assign(resolution="kept final/newest"))
    out = d.drop_duplicates(key, keep="first").drop(columns="_prov")
    return out, (pd.concat(conflicts) if conflicts else pd.DataFrame(columns=list(d.columns) + ["resolution"]))


ROUTE_MEASURES = ["pax", "freight_t", "mail_t"]


def load_routes(man: pd.DataFrame) -> pd.DataFrame | None:
    ok = man[(man.parse_status == "ok") & man.apply(lambda r: (dataset_for(r["title"], r["ext"], r["portal_path"]) or {}).get("dataset") == "city_pair_monthly", axis=1)]
    frames = []
    for _, r in ok.iterrows():
        f = config.INTERIM / "city_pair" / f"{r.content_sha256}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            d["first_seen"] = r.first_seen
            frames.append(d)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d["period"] = pd.to_datetime(d["period"])
    norm = {n: places.normalise(n) for n in set(d.origin) | set(d.destination)}
    d["origin_key"] = d.origin.map(lambda n: norm[n][0])
    d["dest_key"] = d.destination.map(lambda n: norm[n][0])
    keep = ["origin_key", "dest_key", "period", "provisional", "revised", "file_sha256", "first_seen"]
    d = d[keep + ROUTE_MEASURES]
    # two source names can collapse to one place inside the same file (e.g. both Goa airports under an old label): add them
    d = d.groupby(["origin_key", "dest_key", "period", "file_sha256", "provisional", "revised", "first_seen"], as_index=False, dropna=False)[ROUTE_MEASURES].sum(min_count=1)
    return d, norm


def dedupe_routes(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same month published twice (original + 'Revised'): revised beats original, final beats provisional, then newest."""
    key = ["origin_key", "dest_key", "period"]
    d = d.assign(_rank=d.revised.astype(int) * -2 + d.provisional.astype(int)).sort_values(["_rank", "first_seen", "file_sha256"], ascending=[True, False, True], kind="stable")
    dup = d[d.duplicated(key, keep=False)]
    conflicts = dup[dup.groupby(key)["pax"].transform(lambda x: (x.max() - x.min()) > max(1, 0.001 * x.abs().max()))] if len(dup) else dup
    return d.drop_duplicates(key, keep="first").drop(columns="_rank"), conflicts


def build(man: pd.DataFrame) -> dict:
    d = load_parsed(man)
    d["entity_key"] = d["entity"].map(canonical)
    d["airline_key"] = d["entity_key"].map(slug)
    n_in = len(d)
    d, conflicts = dedupe(d)
    n_dupes = n_in - len(d)

    # --- dimensions
    d["fy_start"] = d.period.dt.year - (d.period.dt.month < 4).astype(int)
    d["fy"] = d.fy_start.astype(str) + "-" + ((d.fy_start + 1) % 100).astype(str).str.zfill(2)
    ents = d.groupby(["airline_key", "entity_key"]).period.agg(["min", "max"]).reset_index()
    dim_airline = pd.DataFrame({
        "airline_key": ents.airline_key,
        "airline": ents.entity_key.map(display),
        "airline_type": ents.entity_key.map(lambda k: "Aggregate" if k in AGGREGATES else "Airline"),
        "first_month": ents["min"].dt.date, "last_month": ents["max"].dt.date,
    }).sort_values(["airline_type", "airline"]).reset_index(drop=True)

    routes_pack = load_routes(man)
    route_periods = set()
    route_conflicts = pd.DataFrame()
    if routes_pack is not None:
        rd, norm = routes_pack
        rd, route_conflicts = dedupe_routes(rd)
        route_periods = set(rd.period.unique())
    dom_place_keys = {k for k, _, _ in norm.values()} if routes_pack is not None else set()
    intl = intl_pub.build(man, dom_place_keys)
    if intl is not None:                                  # international tables add months (Jan-Mar 2015) and quarter-start months
        for t in intl["tables"].values():
            if "period_key" in t.columns:
                route_periods |= set(pd.to_datetime(t.period_key.astype(str) + "01", format="%Y%m%d").unique())
    per = pd.DataFrame({"period": sorted(set(d.period.unique()) | route_periods)})
    per["fy_start_year"] = per.period.dt.year - (per.period.dt.month < 4).astype(int)
    per["fy"] = per.fy_start_year.astype(str) + "-" + ((per.fy_start_year + 1) % 100).astype(str).str.zfill(2)
    per["fy_month_num"] = (per.period.dt.month - 4) % 12 + 1
    per["quarter"] = per.fy + " Q" + ((per.fy_month_num - 1) // 3 + 1).astype(str)
    months_in_fy = per.groupby("fy").period.transform("count")
    per["fy"] = per.fy.where(months_in_fy >= 12, per.fy + " (YTD)")        # a partial fiscal year must not read as a collapse
    per["month_name"] = per.period.dt.strftime("%b")
    per["calendar_year"] = per.period.dt.year
    per["period_key"] = per.period.dt.year * 100 + per.period.dt.month
    per["period"] = per.period.dt.date
    dim_period = per[["period_key", "period", "fy", "fy_start_year", "quarter", "month_name", "fy_month_num", "calendar_year"]]
    dim_segment = pd.DataFrame({"segment_key": [1, 2], "segment": ["Domestic", "International"]})

    extra_tables, route_stats = {}, {}
    if routes_pack is not None:
        info = {}
        for raw, (k, disp, city) in sorted(norm.items(), key=lambda kv: kv[0].isupper()):   # mixed-case (newest) spelling wins
            info[k] = (disp, city)
        dim_place = pd.DataFrame([(k, v[0], v[1]) for k, v in sorted(info.items())], columns=["place_key", "place", "city"])
        pair = lambda o, t: " – ".join(sorted([info[o][0], info[t][0]]))
        rd["route_key"] = [("_".join(sorted([o, t]))) for o, t in zip(rd.origin_key, rd.dest_key)]
        pairs = rd[["route_key", "origin_key", "dest_key"]].drop_duplicates("route_key")
        dim_route = pd.DataFrame({"route_key": pairs.route_key.values, "route": [pair(o, t) for o, t in zip(pairs.origin_key, pairs.dest_key)]}).sort_values("route").reset_index(drop=True)
        fact_route = pd.DataFrame({
            "origin_key": rd.origin_key.values, "dest_key": rd.dest_key.values, "route_key": rd.route_key.values,
            "period_key": (rd.period.dt.year * 100 + rd.period.dt.month).values,
            **{m: rd[m].values for m in ROUTE_MEASURES},
            "provisional": rd.provisional.astype(bool).values, "source_sha": rd.file_sha256.str[:12].values,
        }).sort_values(["period_key", "origin_key", "dest_key"]).reset_index(drop=True)
        near = places.near_duplicates(dim_place.place.tolist())
        (config.PROCESSED).mkdir(parents=True, exist_ok=True)
        (config.PROCESSED / "place_warnings.json").write_text(json.dumps({"near_duplicate_places": near}, indent=1))
        extra_tables = {"fact_route_monthly": fact_route, "dim_place": dim_place, "dim_route": dim_route}
        route_stats = {"route_rows": len(fact_route), "places": len(dim_place), "routes": len(dim_route), "place_warnings": len(near)}

    intl_stats = {}
    if intl is not None:
        intl_stats = intl["stats"]
        extra_tables.update(intl["tables"])
        dp = extra_tables.get("dim_place")
        dom_keys = set(dp.place_key) if dp is not None else set()
        add_p = intl["places"][~intl["places"].place_key.isin(dom_keys)] if intl["places"] is not None else None
        if dp is not None and add_p is not None:
            dp = pd.concat([dp, add_p], ignore_index=True)
        elif add_p is not None:
            dp = intl["places"]
        if dp is not None:
            dp["domain"] = dp.place_key.map(lambda k: "India" if k in dom_keys else "Foreign")
            extra_tables["dim_place"] = dp.sort_values("place").reset_index(drop=True)
        dr = extra_tables.get("dim_route")
        if intl["routes"] is not None:
            extra_tables["dim_route"] = pd.concat([dr, intl["routes"][~intl["routes"].route_key.isin(set(dr.route_key) if dr is not None else set())]], ignore_index=True).sort_values("route").reset_index(drop=True) if dr is not None else intl["routes"]
    # --- fact
    seg_key = {"domestic": 1, "international": 2}
    fact = pd.DataFrame({
        "airline_key": d.airline_key.values,
        "period_key": (d.period.dt.year * 100 + d.period.dt.month).values,
        "segment_key": d.segment.map(seg_key).values,
        **{c: d[c].values for c in FACT_COLS},
        "provisional": d.provisional.astype(bool).values,
        "source_sha": d.file_sha256.str[:12].values,
    }).sort_values(["period_key", "airline_key", "segment_key"]).reset_index(drop=True)

    h = hashlib.sha256(pd.util.hash_pandas_object(fact, index=False).values.tobytes())
    h.update(pd.util.hash_pandas_object(man[["s3_url", "title", "status", "parse_status"]].astype(str), index=False).values.tobytes())
    for dim in (dim_airline, dim_period, dim_segment):   # any change to published tables must change the version (cache busting)
        h.update(pd.util.hash_pandas_object(dim.astype(str), index=False).values.tobytes())
    for t in extra_tables.values():
        h.update(pd.util.hash_pandas_object(t.astype(str), index=False).values.tobytes())
    h.update((config.ROOT / "semantic" / "semantic.yaml").read_bytes())
    sit = config.DATA / "site_tables.parquet"
    if sit.exists():
        h.update(pd.util.hash_pandas_object(pd.read_parquet(sit)[["table_sha"]], index=False).values.tobytes())
    version = h.hexdigest()[:10]
    out = config.SITE_DATA / version
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tables = {"fact_airline_monthly": fact, "dim_airline": dim_airline, "dim_period": dim_period, "dim_segment": dim_segment, **extra_tables}
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False, compression="zstd", row_group_size=50_000)

    site_stats = site_pub.build(man, out)

    # --- semantic catalog (validated against what we actually wrote)
    catalog = make_catalog(tables, version)
    catalog["site"] = site_stats
    known = yaml.safe_load((config.ROOT / "semantic" / "semantic.yaml").read_text()).get("known_issues", [])
    catalog["known_issues"] = known
    catalog["checks"] = []
    if "fact_route_monthly" in tables:
        catalog["checks"].append(checks_mod.route_vs_airline(fact, tables["fact_route_monthly"], dim_airline, known))
    if "fact_intl_citypair_quarterly" in tables:
        catalog["checks"].append(checks_mod.intl_internal(tables["fact_intl_airline_monthly"], tables["fact_intl_country_quarterly"], tables["fact_intl_citypair_quarterly"], known))
        catalog["checks"].append(checks_mod.intl_vs_monthly_airline_stats(tables["fact_intl_airline_monthly"], tables["dim_carrier"], tables["fact_intl_citypair_quarterly"], fact, dim_airline, known))
    (out / "catalog.json").write_text(json.dumps(catalog, indent=1))

    # --- provenance + conflicts
    src = man[man.parse_status == "ok"][["content_sha256", "s3_url", "fy", "entity", "table_id", "title", "last_modified"]].copy()
    src["sha12"] = src.content_sha256.str[:12]
    (out / "sources.json").write_text(src.drop(columns="content_sha256").to_json(orient="records"))
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    conflicts.to_parquet(config.PROCESSED / "conflicts.parquet", index=False)
    route_conflicts.to_parquet(config.PROCESSED / "route_conflicts.parquet", index=False)

    (config.SITE_DATA / "latest.json").write_text(json.dumps({"version": version, "built_at": pd.Timestamp.now("UTC").isoformat()}))
    for old in config.SITE_DATA.iterdir():
        if old.is_dir() and old.name != version:
            shutil.rmtree(old)
    print(f"publish: version {version}: {len(fact)} fact rows, {len(dim_airline)} airlines, "
          f"{len(dim_period)} months; {n_dupes} duplicate rows dropped, {len(conflicts)} conflicting rows logged; "
          f"site catalog: {site_stats['files']} files, {site_stats['tables']} tables; routes: {route_stats}; intl: {intl_stats}")
    return {"version": version, "rows": len(fact)}


def make_catalog(tables: dict, version: str) -> dict:
    model = yaml.safe_load((config.ROOT / "semantic" / "semantic.yaml").read_text())
    errors = []
    for fid, f in list(model["facts"].items()):
        if f["table"] not in tables:                         # fact whose data has not been published yet: omit, don't fail
            del model["facts"][fid]
            continue
        cols = set(tables[f["table"]].columns)
        alias_table = {}
        for alias, (fk, dt, dk) in f["joins"].items():
            alias_table[alias] = dt
            if dt not in tables or fk not in cols or dk not in tables[dt].columns:
                errors.append(f"fact {fid}: bad join {alias}")
        for d in f["dimensions"]:
            dm = model["dimensions"][d]
            real = alias_table.get(dm["table"])
            if real is None:
                errors.append(f"fact {fid}: dimension {d} uses alias {dm['table']} which the fact does not join")
                continue
            if dm["column"] not in tables[real].columns:
                errors.append(f"dimension {d}: column {dm['column']} missing in {real}")
            if dm.get("sort_by") and dm["sort_by"] not in tables[real].columns:
                errors.append(f"dimension {d}: sort_by {dm['sort_by']} missing")
        for m in f["measures"]:
            for c in __import__("re").findall(r"f\.(\w+)", model["measures"][m]["expr"]):
                if c not in cols:
                    errors.append(f"measure {m}: fact column {c} missing")
    if errors:
        raise SystemExit("semantic model invalid:\n  " + "\n  ".join(errors))
    used = {d for f in model["facts"].values() for d in f["dimensions"]}
    used_m = {m for f in model["facts"].values() for m in f["measures"]}
    model["dimensions"] = {k: v for k, v in model["dimensions"].items() if k in used}
    model["measures"] = {k: v for k, v in model["measures"].items() if k in used_m}
    for did, dm in model["dimensions"].items():
        real = next(f["joins"][dm["table"]][1] for f in model["facts"].values() if dm["table"] in f["joins"])
        t = tables[real]
        if dm["kind"] == "category":
            sort = dm.get("sort_by") or dm["column"]
            dm["values"] = t.sort_values(sort)[dm["column"]].drop_duplicates().tolist()
        else:
            dm["min"], dm["max"] = str(t[dm["column"]].min()), str(t[dm["column"]].max())
    model["version"] = version
    model["tables"] = {n: list(df.columns) for n, df in tables.items()}
    return model
