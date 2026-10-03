import json
from pathlib import Path

import pandas as pd
import pytest

from dgca_stats import config
from dgca_stats.parse import intl_quarterly as iq
from dgca_stats.parse.monthly import LayoutError
from dgca_stats.parse.registry import dataset_for
from dgca_stats.publish import countries, places
from dgca_stats.publish.names import canonical, display

FX = Path(__file__).parent / "fixtures" / "intl"
ALL = ("pax", "To India")


def named(f, kind):
    return iq.read_named(FX / f, kind)


# ------------------------------------------------------------------ parser: every table type, every layout
@pytest.mark.parametrize("file,kind", [("19Q1_1.xlsx", "airline"), ("19Q1_2.xlsx", "airline_month"), ("19Q1_3.xlsx", "country"),
                                       ("15Q1_1.xlsx", "airline"), ("15Q1_3.xlsx", "country"), ("26Q1_2.xlsx", "airline_month"), ("26Q1_3.xlsx", "country")])
def test_named_tables_reconcile_to_their_own_printed_totals(file, kind):
    p = named(file, kind)
    assert [x for x in iq.validate_named(p) if not x.startswith("warn")] == []
    assert p["totals"], "sheet printed no totals to check against"


def test_known_totals_2019_q1_and_four_way_agreement():
    t1, t2, t3 = named("19Q1_1.xlsx", "airline"), named("19Q1_2.xlsx", "airline_month"), named("19Q1_3.xlsx", "country")
    t4 = iq.citypair(FX / "19Q1_4.xlsx")
    tot = t1["totals"]["all"]
    assert tot[(None, "pax", "To India")] == 7_844_578 and tot[(None, "pax", "From India")] == 8_548_440
    assert tot[(None, "freight_t", "To India")] == pytest.approx(169947.843123)
    sums = {}
    for _, _, v in t2["rows"]:                                            # 3 months x carriers
        for (mo, meas, d), x in v.items():
            sums[(meas, d)] = sums.get((meas, d), 0) + x
    for k, w in sums.items():
        assert w == pytest.approx(tot[(None,) + k], rel=2e-3)                 # months add up to the quarter
        assert sum(v[(None,) + k] for *_, v in t3["rows"]) == pytest.approx(tot[(None,) + k], rel=2e-3)   # countries add up to it
        assert sum(v[k] for b in t4["blocks"] if not b["outside"] for _, _, v in b["rows"]) == pytest.approx(tot[(None,) + k], rel=2e-3)  # so do city pairs


def test_carrier_sections_split_indian_and_foreign():
    p = named("19Q1_1.xlsx", "airline")
    sec = {name: s for s, name, _ in p["rows"]}
    assert sec["AIR INDIA"] == "Indian" and sec["JET AIRWAYS"] == "Indian" and sec["EMIRATES AIRLINE"] == "Foreign"
    assert sum(1 for s, *_ in p["rows"] if s == "Indian") < sum(1 for s, *_ in p["rows"] if s == "Foreign")


def test_footnote_asterisks_are_stripped_and_counted():
    p = named("16Q4_1.xlsx", "airline")
    assert p["footnoted"] == 2
    aie = next(v for _, n, v in p["rows"] if n == "AIR INDIA EXPRESS")
    assert aie[(None, "freight_t", "To India")] == 345.1 and aie[(None, "freight_t", "From India")] == 2027.6


def test_long_format_has_one_row_per_name_period_direction_without_phantom_combinations():
    p = named("19Q1_2.xlsx", "airline_month")
    d = iq.named_long(p, 2019, 1)
    assert set(d.period) == {pd.Timestamp("2019-01-01"), pd.Timestamp("2019-02-01"), pd.Timestamp("2019-03-01")}
    assert not d.duplicated(["name", "period", "direction"]).any() and d.pax.notna().all()
    assert (d[d.name == "INDIGO"].section == "Indian").all()                # no 'INDIGO as Foreign' cartesian rows
    q = iq.named_long(named("19Q1_3.xlsx", "country"), 2019, 1)
    assert set(q.period) == {pd.Timestamp("2019-01-01")}


# ------------------------------------------------------------------ city pairs
def test_citypair_modern_layout_two_blocks_and_directional_rows():
    p = iq.citypair(FX / "19Q1_4.xlsx")
    assert [b["outside"] for b in p["blocks"]] == [False, True]
    assert [x for x in iq.validate_citypair(p) if not x.startswith("warn")] == []
    d = iq.citypair_long(p, 2019, 1)
    row = d[(d.origin == "ABUDHABI") & (d.destination == "AHMEDABAD")]
    assert list(row.direction) == ["To India"] and row.pax.iloc[0] == 12501                  # 'to city 2'
    back = d[(d.origin == "AHMEDABAD") & (d.destination == "ABUDHABI")]
    assert list(back.direction) == ["From India"] and back.pax.iloc[0] == 13739
    assert set(d[d.scope == "outside"].direction) == {"Outside India"}


def test_citypair_2015_layout_titles_without_table_prefix_still_split_blocks():
    p = iq.citypair(FX / "15Q1_4.xlsx")
    assert [b["outside"] for b in p["blocks"]] == [False, True]
    assert p["blocks"][0]["sub"][("pax", "To India")] == 5_712_230 and p["blocks"][1]["sub"][("pax", "To India")] == 54_193
    assert [x for x in iq.validate_citypair(p) if not x.startswith("warn")] == []


@pytest.mark.parametrize("pair", ["18Q3", "15Q3"])
def test_misprinted_footers_are_judged_against_the_independent_table(pair):
    p = iq.citypair(FX / f"{pair}_4.xlsx")
    ref = iq.read_named(FX / f"{pair}_1.xlsx", "airline")["totals"]["all"]
    without = iq.validate_citypair(p)                                       # footers alone: looks broken
    assert any(not x.startswith("warn") for x in without)
    with_ref = iq.validate_citypair(p, ref)                                 # rows agree with table 1 => footer misprint is only a warning
    assert [x for x in with_ref if not x.startswith("warn")] == [] and any(x.startswith("warn") for x in with_ref)


def test_rows_that_disagree_with_the_independent_table_stay_a_hard_error():
    p = iq.citypair(FX / "19Q1_4.xlsx")
    ref = iq.read_named(FX / "19Q1_1.xlsx", "airline")["totals"]["all"]
    p["blocks"][0]["rows"][3][2][("pax", "To India")] += 200_000
    assert any(not x.startswith("warn") for x in iq.validate_citypair(p, ref))


def test_footnoted_freight_shortfall_is_a_warning_not_an_error():
    p = named("16Q4_3.xlsx", "country")
    probs = iq.validate_named(p)
    assert [x for x in probs if not x.startswith("warn")] == [] and any("freight" in x for x in probs)


def test_pax_mismatch_is_never_softened():
    p = named("19Q1_3.xlsx", "country")
    p["rows"][0][2][(None, "pax", "To India")] += 50_000
    assert any(not x.startswith("warn") and "pax" in x for x in iq.validate_named(p))


def test_not_a_quarterly_sheet_is_rejected(tmp_path):
    f = tmp_path / "x.xlsx"
    pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(f, index=False)
    with pytest.raises(LayoutError):
        iq.read_named(f, "airline")


def test_registry_identifies_table_year_quarter():
    base = "jsp/dgca/InventoryList/dataReports/aviationDataStatistics/airTransport/international/quaterly/"
    ds = dataset_for("x", "xlsx", base + "19Q1_4.xlsx")
    assert (ds["dataset"], ds["table"], ds["year"], ds["quarter"]) == ("intl_quarterly", 4, 2019, 1)
    assert dataset_for("x", "pdf", base + "19Q1_4.pdf") is None and dataset_for("x", "xlsx", base + "19Q1_5.xlsx") is None


# ------------------------------------------------------------------ names
@pytest.mark.parametrize("raw,key", [("QATAR AIRWATYS", "QATAR AIRWAYS"), ("EMIRATES AIRLINE", "EMIRATES"), ("SCOOT PTE LTD.", "SCOOT TIGER AIR"),
                                     ("VISTARA AIRLINES", "VISTARA"), ("BUDHHA AIR", "BUDDHA AIR"), ("SRILNKAN AIRWAYS", "SRILANKAN AIRWAYS"),
                                     ("UGANDA AIRLNES", "UGANDA AIRLINES"), ("RWANDA AIR", "RWANDAIR"), ("BLUE DART AVIATION", "BLUE DART"), ("GO AIR", "GO AIR")])
def test_carrier_aliases(raw, key):
    assert canonical(raw) == key


def test_carrier_display_names():
    assert display(canonical("ITA AIRWAYS")) == "ITA Airways" and display(canonical("KLM AIRLINES")) == "KLM Airlines"
    assert display(canonical("AIR ASIA BERHAD")) != display(canonical("AIR ASIA"))            # Malaysian AirAsia is not AirAsia India


@pytest.mark.parametrize("raw,disp", [("UNITED ARAB EMIRATES*", "United Arab Emirates"), ("UNITED ARAB EMIRATES", "United Arab Emirates"),
                                      ("NEW ZELAND", "New Zealand"), ("Estonia", "Estonia"), ("ESTONIA", "Estonia"), ("BURMA", "Myanmar (Burma)")])
def test_country_normalisation(raw, disp):
    assert countries.normalise(raw)[1] == disp


@pytest.mark.parametrize("raw,disp,city", [("ABUDHABI", "Abu Dhabi", "Abu Dhabi"), ("HONGKONG", "Hong Kong", "Hong Kong"), ("HONG KONG", "Hong Kong", "Hong Kong"),
                                           ("LHR", "London (Heathrow)", "London"), ("STANSTED", "London (Stansted)", "London"),
                                           ("DON MUEANG, BANGKOK", "Bangkok (Don Mueang)", "Bangkok"), ("NEWARK, NEW YORK", "Newark (New York)", "New York"),
                                           ("RASALKHAMA", "Ras Al Khaimah", "Ras Al Khaimah"), ("RAS AL-KHAIMAH", "Ras Al Khaimah", "Ras Al Khaimah"),
                                           ("Da Nang", "Da Nang", "Da Nang"), ("DA NANG", "Da Nang", "Da Nang"), ("Shymkent", "Shymkent", "Shymkent"), ("SHYAMKENT", "Shymkent", "Shymkent")])
def test_foreign_city_normalisation(raw, disp, city):
    _, d, c = places.normalise(raw)
    assert (d, c) == (disp, city)


# ------------------------------------------------------------------ published bundle
def _bundle():
    latest = config.SITE_DATA / "latest.json"
    if not latest.exists():
        pytest.skip("no published data")
    d = config.SITE_DATA / json.loads(latest.read_text())["version"]
    if not (d / "fact_intl_airline_monthly.parquet").exists():
        pytest.skip("no international data published")
    return d


def _tables():
    d = _bundle()
    R = lambda t: pd.read_parquet(d / f"{t}.parquet")
    return d, R


def test_international_bundle_integrity():
    d, R = _tables()
    for t, key in (("fact_intl_airline_monthly", ["carrier_key", "period_key", "direction_key"]), ("fact_intl_country_quarterly", ["country_key", "period_key", "direction_key"]),
                   ("fact_intl_citypair_quarterly", ["origin_key", "dest_key", "period_key", "direction_key"])):
        f = R(t)
        assert not f.duplicated(key).any(), t
        assert f.pax.notna().all() and f.freight_t.notna().all() and (f.pax >= 0).all(), t
        assert set(f.period_key) <= set(R("dim_period").period_key), t
    a, c = R("fact_intl_airline_monthly"), R("dim_carrier")
    assert set(a.carrier_key) == set(c.carrier_key)
    assert set(c[c.carrier_origin == "Indian"].carrier) == {"Air India", "Air India Express", "AirAsia India / AIX Connect", "Akasa Air", "Alliance Air", "Blue Dart Aviation", "GoAir", "IndiGo", "Jet Airways", "SpiceJet", "Vistara"}
    assert (c.carrier_origin == "Foreign").sum() > 100
    assert set(R("dim_direction").direction) == {"To India", "From India", "Outside India"}
    assert set(R("fact_intl_country_quarterly").period_key % 100) <= {1, 4, 7, 10}                # quarterly facts sit on quarter-start months
    assert not [n for n in R("dim_country").country if n.endswith("*")]


def test_international_name_hygiene_no_near_duplicates():
    d, R = _tables()
    assert not places.near_duplicates(R("dim_place").place.tolist())
    carriers = R("dim_carrier").carrier.tolist()
    import difflib, re
    keys = sorted({re.sub(r"[^a-z0-9]", "", n.lower()): n for n in carriers}.items())
    dup = [(a, b) for i, (x, a) in enumerate(keys) for y, b in keys[i + 1:] if abs(len(x) - len(y)) <= 2 and difflib.SequenceMatcher(None, x, y).ratio() > 0.93]
    legit = {("Thai Air Asia", "Thai Air Asia X")}                         # different airlines (short- vs long-haul), not spelling variants
    assert [d for d in dup if d not in legit] == [], dup
    assert not [c for c in R("dim_country").country if difflib.SequenceMatcher(None, c.lower(), "new zealand").ratio() > 0.9 and c != "New Zealand"]


def test_known_values_against_sheet_2019_q1():
    d, R = _tables()
    a = R("fact_intl_airline_monthly")
    q = a[a.period_key.between(201901, 201903)]
    assert q[q.direction_key == 1].pax.sum() == pytest.approx(7_844_578, rel=2e-3) and q[q.direction_key == 2].pax.sum() == pytest.approx(8_548_440, rel=2e-3)
    c = R("fact_intl_country_quarterly")
    assert c[(c.period_key == 201901)].pax.sum() == pytest.approx(7_844_578 + 8_548_440, rel=2e-3)


def test_publications_reconcile_exactly_flights_outside_india():
    """monthly airline statistics == quarterly tables + Indian carriers' flights entirely outside India, to the passenger, wherever both exist."""
    d, R = _tables()
    a = R("fact_intl_airline_monthly").merge(R("dim_carrier"), on="carrier_key")
    f = R("fact_airline_monthly").merge(R("dim_airline"), on="airline_key")
    p = R("fact_intl_citypair_quarterly")
    qid = lambda s: (s // 100) * 10 + (((s % 100) - 1) // 3)
    ind = a[a.carrier_origin == "Indian"].assign(q=lambda x: qid(x.period_key)).groupby("q").pax.sum()
    mon = f[(f.airline_type == "Airline") & (f.segment_key == 2)].assign(q=lambda x: qid(x.period_key)).groupby("q").pax.sum()
    out = p[p.direction_key == 3].assign(q=lambda x: qid(x.period_key)).groupby("q").pax.sum()
    j = pd.concat([ind.rename("q"), out.rename("o"), mon.rename("m")], axis=1).dropna()
    clean = j[(j.index >= 20160) & (j.index <= 20184)]            # years with no documented source disagreement
    assert len(clean) == 12
    assert ((clean.m - clean.q - clean.o).abs() <= 0.0005 * clean.m).all(), (clean.m - clean.q - clean.o).abs()
    assert (j.m / (j.q + j.o) - 1).abs().median() < 1e-4


def test_catalog_checks_and_known_issues_present():
    d, R = _tables()
    cat = json.loads((d / "catalog.json").read_text())
    by = {c["name"]: c for c in cat["checks"]}
    assert {"route_vs_airline", "intl_internal", "intl_vs_monthly_airline_stats"} <= set(by)
    assert all(c["unexplained"] == 0 for c in cat["checks"])
    assert by["intl_internal"]["months_compared"] == 91 and by["intl_internal"]["within_tolerance"] == 91   # 46 quarters x 2, minus the missing Q1 2026 city-pair file
    assert 28 <= by["intl_vs_monthly_airline_stats"]["within_tolerance"] <= by["intl_vs_monthly_airline_stats"]["months_compared"]
    dev = {x["label"] for x in by["intl_vs_monthly_airline_stats"]["deviations"]}
    assert {"2022Q1", "2022Q4", "2024Q2", "2019Q3"} <= dev and all(x["explained"] for x in by["intl_vs_monthly_airline_stats"]["deviations"])


def test_foreign_to_foreign_rows_in_india_block_are_tagged_outside_india():
    d, R = _tables()
    p = R("fact_intl_citypair_quarterly").merge(R("dim_place")[["place_key", "domain"]].rename(columns={"place_key": "origin_key", "domain": "od"}), on="origin_key")
    p = p.merge(R("dim_place")[["place_key", "domain"]].rename(columns={"place_key": "dest_key", "domain": "dd"}), on="dest_key")
    assert not ((p.direction_key == 1) & (p.od == "India")).any() and not ((p.direction_key == 1) & (p.dd == "Foreign")).any()    # To India: foreign -> India
    assert not ((p.direction_key == 2) & (p.od == "Foreign")).any() and not ((p.direction_key == 2) & (p.dd == "India")).any()
    out = p[p.direction_key == 3]
    assert len(out) > 0 and (out.od == "Foreign").all() and (out.dd == "Foreign").all()
    rc = p[p.reclassified]
    assert 5 <= rc.route_key.nunique() <= 12 and (rc.direction_key == 3).all()
