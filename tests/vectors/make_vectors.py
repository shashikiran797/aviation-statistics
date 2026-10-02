"""Regenerate tests/vectors/spec_cases.json from the Python reference compiler (review the diff before committing)."""
import json, pathlib, sys
from dgca_stats.spec import SpecError, compile_spec
root = pathlib.Path(__file__).resolve().parents[2]
cat = json.loads(next((root / "web/data").glob("*/catalog.json")).read_text())
cases = [
 ("kpi_total_pax", {"fact": "airline_monthly", "measures": ["pax"]}),
 ("pax_by_fy", {"fact": "airline_monthly", "dimensions": ["fy"], "measures": ["pax"]}),
 ("monthly_timeline_domestic", {"fact": "airline_monthly", "dimensions": ["period"], "measures": ["pax", "pax_load_factor"], "filters": [{"dim": "segment", "op": "in", "values": ["Domestic"]}]}),
 ("top5_airlines_by_month", {"fact": "airline_monthly", "dimensions": ["month", "airline"], "measures": ["pax"], "filters": [{"dim": "fy", "op": "in", "values": ["2018-19"]}], "top": {"dim": "airline", "measure": "pax", "n": 5}}),
 ("sorted_limited", {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax", "pax_per_departure"], "sort": {"by": "pax", "dir": "desc"}, "limit": 10}),
 ("aggregates_only_override", {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax"], "filters": [{"dim": "airline_type", "op": "in", "values": ["Aggregate"]}]}),
 ("date_between", {"fact": "airline_monthly", "dimensions": ["period"], "measures": ["departures"], "filters": [{"dim": "period", "op": "between", "values": ["2017-04-01", "2018-03-01"]}]}),
 ("quote_escaping", {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax"], "filters": [{"dim": "airline", "op": "not_in", "values": ["O'Hare Air", "A"]}]}),
 ("err_unknown_measure", {"fact": "airline_monthly", "measures": ["nope"]}),
 ("err_injection_dim", {"fact": "airline_monthly", "dimensions": ["airline; DROP TABLE x"], "measures": ["pax"]}),
 ("err_bad_date", {"fact": "airline_monthly", "dimensions": ["period"], "measures": ["pax"], "filters": [{"dim": "period", "op": "between", "values": ["x'--", "2018-03-01"]}]}),
 ("err_empty", {"fact": "airline_monthly"}),
]
out = []
for name, spec in cases:
    try:
        out.append({"name": name, "spec": spec, "sql": compile_spec(cat, spec)})
    except SpecError as e:
        out.append({"name": name, "spec": spec, "error": True})
(root / "tests/vectors/spec_cases.json").write_text(json.dumps({"catalog_version": cat["version"], "cases": out}, indent=1))
(root / "tests/vectors/catalog.json").write_text(json.dumps(cat))
print(len(out), "vectors")
