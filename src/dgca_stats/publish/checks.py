"""Cross-dataset consistency checks run at publish time and recorded in the catalog.

Domestic passengers appear twice: summed over airlines (airline_monthly) and summed over city pairs (route_monthly, both directions).
They are independent publications, so they must agree. Disagreements either match a documented known issue or fail validation.
"""
import pandas as pd

TOLERANCE = 0.01


def _as_list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def _quarter(pk: pd.Series) -> pd.Series:
    """yyyymm -> 'YYYYQn' (calendar quarters, as the source tables are published)."""
    return (pk // 100).astype(str) + "Q" + (((pk % 100) - 1) // 3 + 1).astype(str)


def _in_known(known, check, quarter_label):
    y, q = int(quarter_label[:4]), int(quarter_label[-1])
    first = y * 100 + 3 * (q - 1) + 1
    for k in known:
        if check in _as_list(k.get("explains")) and k.get("from") and int(k["from"].replace("-", "")) <= first <= int(k["to"].replace("-", "")):
            return k["note"]
    return ""


def intl_internal(fact_air, fact_cty, fact_cp, known: list[dict]) -> dict:
    """The four quarterly tables describe the same traffic: carrier months, countries and city pairs must add up to each other."""
    a = fact_air.assign(q=_quarter(fact_air.period_key)).groupby("q")[["pax", "freight_t"]].sum()
    c = fact_cty.assign(q=_quarter(fact_cty.period_key)).groupby("q")[["pax", "freight_t"]].sum()
    p = fact_cp[fact_cp.block == "A"]
    p = p.assign(q=_quarter(p.period_key)).groupby("q")[["pax", "freight_t"]].sum()
    devs, compared, ok = [], 0, 0
    for q in a.index:
        for name, other in (("countries", c), ("city pairs", p)):
            if q not in other.index:
                continue                                     # table not published for that quarter
            compared += 1
            bad = []
            for m, tol in (("pax", 0.002), ("freight_t", 0.015)):
                r = other.loc[q, m] / a.loc[q, m] if a.loc[q, m] else float("nan")
                if abs(r - 1) > tol:
                    bad.append(f"{m} ratio {r:.4f}")
            if bad:
                devs.append({"period_key": q, "label": f"{q} {name}", "ratio": 0, "explained": bool(_in_known(known, "intl_internal", q)), "note": _in_known(known, "intl_internal", q) or "; ".join(bad)})
            else:
                ok += 1
    return {"name": "intl_internal", "unit": "table pairs", "description": "International tables, per quarter: carriers vs countries vs city pairs",
            "months_compared": compared, "within_tolerance": ok, "tolerance": 0.002, "median_ratio": 1.0, "deviations": devs,
            "unexplained": sum(1 for d in devs if not d["explained"])}


def intl_vs_monthly_airline_stats(fact_air, dim_carrier, fact_cp, fact_airline, dim_airline, known: list[dict]) -> dict:
    """Two separate DGCA publications of Indian carriers' international passengers.

    Monthly airline statistics count every passenger carried on scheduled international services, including Indian carriers' flights
    *entirely outside India*; the quarterly tables list those separately (city-pair block B). So per quarter:
        monthly statistics (3 months) == quarterly Indian-carrier passengers + outside-India block."""
    ind = fact_air.merge(dim_carrier[["carrier_key", "carrier_origin"]], on="carrier_key")
    ind = ind[ind.carrier_origin == "Indian"]
    qtr = ind.assign(q=_quarter(ind.period_key)).groupby("q").pax.sum()
    outside = fact_cp[fact_cp.direction_key == 3]
    out = outside.assign(q=_quarter(outside.period_key)).groupby("q").pax.sum()
    have_cp = set(_quarter(fact_cp.period_key))
    m = fact_airline.merge(dim_airline[["airline_key", "airline_type"]], on="airline_key")
    m = m[(m.airline_type == "Airline") & (m.segment_key == 2)]
    mon = m.assign(q=_quarter(m.period_key)).groupby("q").pax.sum()
    devs, compared, ok, ratios = [], 0, 0, []
    for q in qtr.index:
        if q not in have_cp or q not in mon.index:
            continue
        compared += 1
        expect = qtr[q] + out.get(q, 0.0)
        r = mon[q] / expect
        ratios.append(r)
        if abs(r - 1) > 0.001:
            note = _in_known(known, "intl_vs_monthly_airline_stats", q)
            devs.append({"period_key": q, "label": q, "ratio": round(float(r), 4), "explained": bool(note), "note": note})
        else:
            ok += 1
    return {"name": "intl_vs_monthly_airline_stats", "unit": "quarters",
            "description": "Indian carriers' international passengers: monthly airline statistics vs quarterly tables + flights outside India",
            "months_compared": compared, "within_tolerance": ok, "tolerance": 0.001, "median_ratio": round(float(pd.Series(ratios).median()), 5),
            "deviations": devs, "unexplained": sum(1 for d in devs if not d["explained"])}


def route_vs_airline(fact, fact_route, dim_airline, known: list[dict]) -> dict:
    air = fact.merge(dim_airline[["airline_key", "airline_type"]], on="airline_key")
    air = air[(air.airline_type == "Airline") & (air.segment_key == 1)].groupby("period_key").pax.sum()
    rt = fact_route.groupby("period_key").pax.sum()
    j = pd.concat([rt.rename("route_pax"), air.rename("airline_pax")], axis=1).dropna()
    j["ratio"] = j.route_pax / j.airline_pax
    explain = {}
    for k in known:
        if "route_vs_airline" in _as_list(k.get("explains")):
            lo, hi = int(k["from"].replace("-", "")), int(k["to"].replace("-", ""))
            for pk in j.index:
                if lo <= pk <= hi:
                    explain[pk] = k["note"]
    bad = j[(j.ratio - 1).abs() > TOLERANCE]
    devs = [{"period_key": int(pk), "route_pax": float(r.route_pax), "airline_pax": float(r.airline_pax), "ratio": round(float(r.ratio), 4),
             "explained": pk in explain, "note": explain.get(pk, "")} for pk, r in bad.iterrows()]
    return {"name": "route_vs_airline", "description": "Domestic passengers: sum of city pairs vs sum of airlines, per month",
            "months_compared": int(len(j)), "within_tolerance": int(len(j) - len(bad)), "tolerance": TOLERANCE,
            "median_ratio": round(float(j.ratio.median()), 5), "deviations": devs,
            "unexplained": int(sum(1 for d in devs if not d["explained"]))}
