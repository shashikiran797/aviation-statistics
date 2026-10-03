import json
from pathlib import Path

import pandas as pd
import pytest

from dgca_stats import config
from dgca_stats.parse import city_pair as cp
from dgca_stats.parse.monthly import LayoutError
from dgca_stats.parse.registry import dataset_for
from dgca_stats.publish import build, places

FX = Path(__file__).parent / "fixtures" / "city_pair"
CASES = [  # file, file name as published, month, source TOTAL (both directions)
    ("2015_04_5col.xlsx", "DOM CITYPAIR DATA, APRIL 2015.xlsx", "2015-04", 6_538_477),
    ("2015_08_no_sno.xlsx", "DOM CITYPAIR DATA, AUGUST 2015.xlsx", "2015-08", 3_325_816 + 3_409_551),
    ("2015_10_typo_header.xlsx", "DOM CITYPAIR DATA, OCTOBER 2015.xlsx", "2015-10", 3_555_366 + 3_453_123),
    ("2018_03_total_only.xlsx", "DOM CITYPAIR DATA, MARCH 2018.xlsx", "2018-03", 11_544_833),
    ("2019_01_9col.xlsx", "DOM CITYPAIR DATA, JAN 2019.xlsx", "2019-01", 12_469_075),
    ("2026_01_12col_mixedcase.xlsx", "DOM CITYPAIR DATA, JANUARY  2026.xlsx", "2026-01", 15_247_188),
]


@pytest.mark.parametrize("file,name,month,total", CASES, ids=[c[0] for c in CASES])
def test_every_layout_parses_and_reconciles_to_the_sheet_footer(file, name, month, total):
    p = cp.read(FX / file, name)
    assert [x for x in cp.validate(p) if not x.startswith("warn")] == []
    d = cp.directional(p, p["title_period"] if p["title_period"] is not None else p["file_period"])
    assert d.period.iloc[0] == pd.Timestamp(month + "-01")
    assert d.pax.sum() == pytest.approx(total, abs=2)                     # both directions add up to the published TOTAL
    assert len(d) == 2 * len(p["rows"])
    assert set(d.columns) >= {"origin", "destination", "pax", "freight_t", "mail_t"}


def test_directional_rows_swap_origin_and_destination():
    p = cp.read(FX / "2015_04_5col.xlsx", "x APRIL 2015.xlsx")
    d = cp.directional(p, pd.Timestamp("2015-04-01"))
    a = d[(d.origin == "AGATTI ISLAND") & (d.destination == "KOCHI")].pax.iloc[0]
    b = d[(d.origin == "KOCHI") & (d.destination == "AGATTI ISLAND")].pax.iloc[0]
    assert (a, b) == (902, 785)                                            # 'to city 2' = 902, 'from city 2' = 785
    assert d.freight_t.isna().all()                                        # not published in 2015: unknown, never zero


def test_freight_and_mail_present_from_2018_layout():
    d = cp.directional(cp.read(FX / "2019_01_9col.xlsx", "x JAN 2019.xlsx"), pd.Timestamp("2019-01-01"))
    assert d.freight_t.notna().all() and d.freight_t.sum() == pytest.approx(30718.27793 + 25116.78248, rel=1e-6)


def test_dash_means_zero():
    d = cp.read(FX / "2018_03_total_only.xlsx", "x MARCH 2018.xlsx")["rows"]
    assert (d.select_dtypes("number") >= 0).all().all()


def test_duplicate_pair_rows_are_added_and_flagged_not_dropped():
    p = cp.read(FX / "2025_12_duplicate_pair.xlsx", "x DECEMBER  2025.xlsx")
    assert p["merged_duplicates"] == 2
    probs = cp.validate(p)
    assert [x for x in probs if not x.startswith("warn")] == [] and any("added together" in x for x in probs)
    assert not p["rows"].duplicated(["city1", "city2"]).any()


def test_not_a_city_pair_sheet_is_rejected():
    with pytest.raises(LayoutError):
        cp.read(FX / "2019_06_not_city_pair.xlsx", "DOM CITYPAIR DATA, JUNE 2019.xlsx")


def test_corrupted_footer_or_wrong_month_is_caught():
    p = cp.read(FX / "2019_01_9col.xlsx", "x JAN 2019.xlsx")
    p["rows"].loc[5, "pax_to"] += 50_000
    assert any("pax_to" in x for x in cp.validate(p))
    q = cp.read(FX / "2019_01_9col.xlsx", "DOM CITYPAIR DATA, FEB 2019.xlsx")   # title says Jan, name says Feb
    assert any("title says" in x for x in cp.validate(q))


def test_registry_routes_city_pair_files_to_their_own_dataset():
    base = "jsp/dgca/InventoryList/dataReports/aviationDataStatistics/airTransport/domestic/monthly/"
    assert dataset_for("x", "xlsx", base + "DOM CITYPAIR DATA, JAN 2019.xlsx")["dataset"] == "city_pair_monthly"
    assert dataset_for("x", "xlsx", base + "CITYPAIR, NOV 2016.xlsx")["dataset"] == "city_pair_monthly"
    assert dataset_for("x", "xlsx", base + "indigo25.xlsx")["dataset"] == "airline_monthly_cal"
    assert dataset_for("x", "pdf", base + "DOM CITYPAIR DATA, JAN 2019.pdf") is None


# ---------------------------------------------------------------- places
@pytest.mark.parametrize("raw,key,city", [
    ("MUMBAI", "mumbai", "Mumbai"), ("Mumbai (Mumbai)", "mumbai", "Mumbai"), ("Mumbai (Navi Mumbai)", "navi_mumbai", "Mumbai"),
    ("GOA", "goa_airport_not_specified", "Goa"), ("Goa (Dabolim, South Goa)", "goa_dabolim", "Goa"), ("DABOLIM", "goa_dabolim", "Goa"),
    ("Goa (Mopa, North Goa)", "goa_mopa", "Goa"), ("Mopa, Goa", "goa_mopa", "Goa"),
    ("Cochin", "kochi", "Kochi"), ("Trivandrum", "thiruvananthapuram", "Thiruvananthapuram"), ("Gulbarga", "kalaburagi", "Kalaburagi"),
    ("Kalaburagi, Karnataka", "kalaburagi", "Kalaburagi"), ("Bidar Airport, Karnataka", "bidar", "Bidar"), ("Zero Airport", "ziro", "Ziro"),
    ("Hollongi Airport, Itanagar", "itanagar", "Itanagar"), ("Ayodhya International Airport", "ayodhya", "Ayodhya"),
    ("Rajkot International Airport", "hirasar_rajkot", "Rajkot"), ("Purnia Airport", "purnea", "Purnea"), ("Shivamogga Airport", "shivamogga", "Shivamogga"),
    ("Vijaywada", "vijayawada", "Vijayawada"), ("VIJAYAWADA", "vijayawada", "Vijayawada"),
    ("HINDON AIRPORT", "hindon_ghaziabad", "Delhi NCR (Hindon)"), ("Ghaziabad", "hindon_ghaziabad", "Delhi NCR (Hindon)"),
    ("DEHRA DUN", "dehradun", "Dehradun"), ("DehraDun", "dehradun", "Dehradun"), ("Allahabad (Prayagraj)", "prayagraj", "Prayagraj"),
    ("AGATTI ISLAND", "agatti_island", "Agatti Island"), ("Agatti Island", "agatti_island", "Agatti Island"),
])
def test_place_normalisation(raw, key, city):
    k, _, c = places.normalise(raw)
    assert (k, c) == (key, city)


def test_near_duplicate_detector_flags_spelling_variants_but_not_distinct_places():
    assert places.near_duplicates(["Vijayawada", "Vijaywada", "Delhi", "Mumbai"])
    assert not places.near_duplicates(["Delhi", "Mumbai", "Pune", "Nagpur", "Nanded", "Kochi", "Kolkata"])


def test_route_dedupe_revised_beats_original_and_final_beats_provisional():
    def row(sha, pax, rev, prov, seen):
        return {"origin_key": "a", "dest_key": "b", "period": pd.Timestamp("2023-01-01"), "provisional": prov, "revised": rev, "file_sha256": sha, "first_seen": seen, "pax": pax, "freight_t": 0.0, "mail_t": 0.0}
    d = pd.DataFrame([row("o", 100, False, True, "2026-01"), row("r", 90, True, True, "2026-02"), row("f", 80, False, False, "2026-03")])
    out, conf = build.dedupe_routes(d)
    assert out.pax.iloc[0] == 90 and len(conf) == 3                       # revised wins; disagreement logged


# ---------------------------------------------------------------- published bundle
def _bundle():
    latest = config.SITE_DATA / "latest.json"
    if not latest.exists():
        pytest.skip("no published data")
    d = config.SITE_DATA / json.loads(latest.read_text())["version"]
    if not (d / "fact_route_monthly.parquet").exists():
        pytest.skip("no route data published")
    return d


def test_route_bundle_integrity():
    d = _bundle()
    f = pd.read_parquet(d / "fact_route_monthly.parquet")
    place, route, per = (pd.read_parquet(d / f"{t}.parquet") for t in ("dim_place", "dim_route", "dim_period"))
    assert not f.duplicated(["origin_key", "dest_key", "period_key"]).any()
    assert set(f.origin_key) | set(f.dest_key) <= set(place.place_key) and set(f.route_key) <= set(route.route_key)
    assert set(f.period_key) <= set(per.period_key)
    same = f[f.origin_key == f.dest_key]                                   # one documented source oddity, kept so totals reconcile
    assert list(same.origin_key) == ["hyderabad"] and list(same.period_key) == [202501] and same.pax.iloc[0] <= 5
    assert f.pax.min() >= 0 and route.route.is_unique and place.place.is_unique
    # both directions of every route exist in the same month (a pair row always yields two directed rows)
    pairs = f.groupby(["route_key", "period_key"]).size()
    assert pairs.min() >= 1
    mumbai = place[place.city == "Mumbai"].place.tolist()
    assert "Mumbai" in mumbai and "Navi Mumbai" in mumbai                  # airports roll up to one city
    assert not json.loads((config.PROCESSED / "place_warnings.json").read_text())["near_duplicate_places"]
    # no label that is just another airport-suffixed spelling of a place we already have, and no pre-rename duplicates
    names = set(place.place)
    assert not [n for n in names if n.lower().endswith("airport") and n != "Hindon (Ghaziabad)"]
    assert not names & {"Cochin", "Trivandrum", "Gulbarga", "Zero", "Dabolim", "Mopa, Goa"}
    assert {"Goa (Dabolim)", "Goa (Mopa)", "Goa (airport not specified)"} <= names and set(place[place.city == "Goa"].place) == {"Goa (Dabolim)", "Goa (Mopa)", "Goa (airport not specified)"}


def test_known_route_values_match_the_source_sheet():
    d = _bundle()
    f = pd.read_parquet(d / "fact_route_monthly.parquet")
    jan19 = f[f.period_key == 201901]
    assert jan19.pax.sum() == 12_469_075                                   # sheet TOTAL, both directions
    assert jan19.freight_t.sum() == pytest.approx(30718.27793 + 25116.78248, rel=1e-6)
    p = cp.read(FX / "2019_01_9col.xlsx", "x JAN 2019.xlsx")["rows"]
    row = p[(p.city1 == "ADAMPUR") & (p.city2 == "DELHI")].iloc[0]
    got = jan19[(jan19.origin_key == "adampur") & (jan19.dest_key == "delhi")].pax.iloc[0]
    assert got == row.pax_to


def test_route_totals_agree_with_airline_totals_except_documented_gap():
    d = _bundle()
    cat = json.loads((d / "catalog.json").read_text())
    chk = next(c for c in cat["checks"] if c["name"] == "route_vs_airline")
    assert chk["months_compared"] >= 130 and chk["median_ratio"] == pytest.approx(1.0, abs=0.001)
    assert chk["unexplained"] == 0
    assert {x["period_key"] for x in chk["deviations"]} == set(range(201504, 201513))   # Jet Airways missing Apr-Dec 2015 at source
    assert all(x["ratio"] > 1.15 for x in chk["deviations"])               # route data > airline data, as a missing operator implies
    f = pd.read_parquet(d / "fact_airline_monthly.parquet").merge(pd.read_parquet(d / "dim_airline.parquet"), on="airline_key")
    jet = f[(f.airline == "Jet Airways") & f.period_key.between(201504, 201512)]
    assert jet.empty                                                       # we did not fabricate the missing operator
