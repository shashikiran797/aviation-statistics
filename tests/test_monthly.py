import pandas as pd
from dgca_stats.parse.monthly import MONTHS

FIX = "tests/fixtures/monthly_domestic_2017-18.csv"


def test_monthly_sums_match_annual_total():
    d = pd.read_csv(FIX)
    for airline, g in d.groupby("airline"):
        assert set(g.month) == set(MONTHS) | {"TOTAL"}
        assert g[g.month != "TOTAL"].pax.sum() == g[g.month == "TOTAL"].pax.iloc[0]
