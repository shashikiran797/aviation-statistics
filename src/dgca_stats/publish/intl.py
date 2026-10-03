"""Publish the quarterly international traffic tables as three facts + shared dimensions.

  fact_intl_airline_monthly      carrier x month x direction           (table 2; table 1 is its quarterly roll-up, used for checks)
  fact_intl_country_quarterly    country x quarter x direction         (table 3)
  fact_intl_citypair_quarterly   directed city pair x quarter x direction (table 4; includes Indian carriers' flights entirely outside India)
Direction is 'To India' / 'From India' (or 'Outside India' for foreign-to-foreign flights by Indian carriers).
"""
import re

import pandas as pd

from .. import config
from ..parse.registry import dataset_for
from . import countries, places
from .names import canonical, display, slug

DIRECTION = {"To India": 1, "From India": 2, "Outside India": 3}
SUB = {1: "intl_airline_quarterly", 2: "intl_airline_monthly", 3: "intl_country_quarterly", 4: "intl_citypair_quarterly"}


def _load(man: pd.DataFrame, table: int) -> pd.DataFrame | None:
    frames = []
    for _, r in man[man.parse_status == "ok"].iterrows():
        ds = dataset_for(r["title"], r["ext"], r["portal_path"])
        if not ds or ds.get("dataset") != "intl_quarterly" or ds["table"] != table:
            continue
        f = config.INTERIM / SUB[table] / f"{r.content_sha256}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            d["first_seen"] = r.first_seen
            frames.append(d)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d["period"] = pd.to_datetime(d["period"])
    return d


def _newest(d: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Sum rows that collapse onto one key *within a file* (merged aliases), then keep the newest file per key."""
    num = [c for c in ("pax", "freight_t") if c in d]
    g = d.groupby(key + ["file_sha256", "provisional", "first_seen"], as_index=False, dropna=False)[num].sum(min_count=1)
    return g.sort_values("first_seen", ascending=False, kind="stable").drop_duplicates(key, keep="first")


def period_key(s: pd.Series) -> pd.Series:
    return s.dt.year * 100 + s.dt.month


def build(man: pd.DataFrame, domestic_place_keys: set[str]) -> dict | None:
    t1, t2, t3, t4 = (_load(man, i) for i in (1, 2, 3, 4))
    if t2 is None:
        return None
    out = {"tables": {}, "places": None, "routes": None, "stats": {}, "t1": t1}
    dirkey = lambda s: s.map(DIRECTION)

    # ---- carriers (table 2)
    t2["carrier_key"] = t2.name.map(lambda n: slug(canonical(n)))
    origin = t2.groupby("carrier_key").section.agg(lambda s: s.mode().iat[0] if s.notna().any() else "Foreign")
    shown = t2.sort_values("first_seen").groupby("carrier_key").name.agg(lambda s: display(canonical(s.iloc[-1])))
    span = t2.groupby("carrier_key").period.agg(["min", "max"])
    dim_carrier = pd.DataFrame({"carrier_key": origin.index, "carrier": shown.reindex(origin.index).values, "carrier_origin": origin.values,
                                "first_month": span.reindex(origin.index)["min"].dt.date.values, "last_month": span.reindex(origin.index)["max"].dt.date.values})
    dim_carrier = dim_carrier.sort_values(["carrier_origin", "carrier"]).reset_index(drop=True)
    air = _newest(t2, ["carrier_key", "period", "direction"])
    fact_air = pd.DataFrame({"carrier_key": air.carrier_key.values, "period_key": period_key(air.period).values, "direction_key": dirkey(air.direction).values,
                             "pax": air.pax.values, "freight_t": air.freight_t.values, "provisional": air.provisional.astype(bool).values,
                             "source_sha": air.file_sha256.str[:12].values}).sort_values(["period_key", "carrier_key", "direction_key"]).reset_index(drop=True)
    out["tables"].update(fact_intl_airline_monthly=fact_air, dim_carrier=dim_carrier)

    # ---- countries (table 3)
    if t3 is not None:
        t3["country_key"] = t3.name.map(lambda n: countries.normalise(n)[0])
        cdisp = {countries.normalise(n)[0]: countries.normalise(n)[1] for n in t3.name}
        cty = _newest(t3, ["country_key", "period", "direction"])
        dim_country = pd.DataFrame({"country_key": sorted(cdisp), "country": [cdisp[k] for k in sorted(cdisp)]}).sort_values("country").reset_index(drop=True)
        fact_cty = pd.DataFrame({"country_key": cty.country_key.values, "period_key": period_key(cty.period).values, "direction_key": dirkey(cty.direction).values,
                                 "pax": cty.pax.values, "freight_t": cty.freight_t.values, "provisional": cty.provisional.astype(bool).values,
                                 "source_sha": cty.file_sha256.str[:12].values}).sort_values(["period_key", "country_key", "direction_key"]).reset_index(drop=True)
        out["tables"].update(fact_intl_country_quarterly=fact_cty, dim_country=dim_country)

    # ---- city pairs (table 4)
    if t4 is not None:
        norm = {n: places.normalise(n) for n in set(t4.origin) | set(t4.destination)}
        t4["origin_key"] = t4.origin.map(lambda n: norm[n][0])
        t4["dest_key"] = t4.destination.map(lambda n: norm[n][0])
        t4["block"] = t4.scope.map({"india": "A", "outside": "B"})
        # DGCA lists a handful of foreign-to-foreign pairs in the to/from-India block (e.g. Bucharest-Istanbul): they are not flights to India
        both_foreign = (t4.block == "A") & ~t4.origin_key.isin(domestic_place_keys) & ~t4.dest_key.isin(domestic_place_keys)
        t4["reclassified"] = both_foreign
        t4.loc[both_foreign, "direction"] = "Outside India"
        cp = _newest(t4.assign(block=t4.block), ["origin_key", "dest_key", "period", "direction", "block", "reclassified"])
        info = {}
        for raw, (k, disp, city) in sorted(norm.items(), key=lambda kv: kv[0].isupper()):
            info[k] = (disp, city)
        cp["route_key"] = ["_".join(sorted([o, d])) for o, d in zip(cp.origin_key, cp.dest_key)]
        fact_cp = pd.DataFrame({"origin_key": cp.origin_key.values, "dest_key": cp.dest_key.values, "route_key": cp.route_key.values,
                                "period_key": period_key(cp.period).values, "direction_key": dirkey(cp.direction).values,
                                "pax": cp.pax.values, "freight_t": cp.freight_t.values, "block": cp.block.values, "reclassified": cp.reclassified.values,
                                "provisional": cp.provisional.astype(bool).values, "source_sha": cp.file_sha256.str[:12].values}
                               ).sort_values(["period_key", "origin_key", "dest_key", "direction_key"]).reset_index(drop=True)
        out["tables"]["fact_intl_citypair_quarterly"] = fact_cp
        out["places"] = pd.DataFrame([(k, v[0], v[1]) for k, v in info.items()], columns=["place_key", "place", "city"])
        pr = cp[["route_key", "origin_key", "dest_key"]].drop_duplicates("route_key")
        out["routes"] = pd.DataFrame({"route_key": pr.route_key.values,
                                      "route": [" – ".join(sorted([info[o][0], info[d][0]])) for o, d in zip(pr.origin_key, pr.dest_key)]})

    out["tables"]["dim_direction"] = pd.DataFrame({"direction_key": list(DIRECTION.values()), "direction": list(DIRECTION)})
    out["stats"] = {k: len(v) for k, v in out["tables"].items() if k.startswith("fact_")}
    return out
