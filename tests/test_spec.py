import glob
import json
from pathlib import Path

import duckdb
import pytest

from dgca_stats.spec import SpecError, compile_spec

V = Path(__file__).parent / "vectors"
CASES = json.loads((V / "spec_cases.json").read_text())["cases"]
CAT = json.loads((V / "catalog.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_vectors_match_reference(case):
    if case.get("error"):
        with pytest.raises(SpecError):
            compile_spec(CAT, case["spec"])
    else:
        assert compile_spec(CAT, case["spec"]) == case["sql"]


def test_default_filter_excludes_aggregates_unless_overridden():
    base = {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax"]}
    assert "airline_type IN ('Airline')" in compile_spec(CAT, base)
    over = dict(base, filters=[{"dim": "airline_type", "op": "in", "values": ["Aggregate"]}])
    sql = compile_spec(CAT, over)
    assert "('Aggregate')" in sql and "('Airline')" not in sql


def test_values_cannot_break_out_of_literals():
    spec = {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax"],
            "filters": [{"dim": "airline", "op": "in", "values": ["x'); DROP TABLE dim_airline; --"]}]}
    sql = compile_spec(CAT, spec)
    assert "'x''); DROP TABLE dim_airline; --'" in sql
