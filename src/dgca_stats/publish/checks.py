"""Cross-dataset consistency checks run at publish time and recorded in the catalog.

Domestic passengers appear twice: summed over airlines (airline_monthly) and summed over city pairs (route_monthly, both directions).
They are independent publications, so they must agree. Disagreements either match a documented known issue or fail validation.
"""
import pandas as pd

TOLERANCE = 0.01


def route_vs_airline(fact, fact_route, dim_airline, known: list[dict]) -> dict:
    air = fact.merge(dim_airline[["airline_key", "airline_type"]], on="airline_key")
    air = air[(air.airline_type == "Airline") & (air.segment_key == 1)].groupby("period_key").pax.sum()
    rt = fact_route.groupby("period_key").pax.sum()
    j = pd.concat([rt.rename("route_pax"), air.rename("airline_pax")], axis=1).dropna()
    j["ratio"] = j.route_pax / j.airline_pax
    explain = {}
    for k in known:
        if k.get("explains") == "route_vs_airline":
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
