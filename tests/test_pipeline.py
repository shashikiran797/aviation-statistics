from pathlib import Path

import pandas as pd
import pytest

from dgca_stats import config, manifest
from dgca_stats.crawl import discover
from dgca_stats.parse import monthly
from dgca_stats.parse.registry import dataset_for
from dgca_stats.publish import build
from dgca_stats.publish.names import canonical, display

FX = Path(__file__).parent / "fixtures"


def test_entity_page_yields_pdf_and_xlsx_rows_with_s3_urls():
    html = (FX / "html/entity_indigo_2017-18.html").read_text()
    rows = discover.file_rows(html, "page.html", "INDIGO", "2017-18")
    t101 = [r for r in rows if r["table_id"] == "1.01"]
    assert {r["ext"] for r in t101} == {"pdf", "xlsx"}
    x = next(r for r in t101 if r["ext"] == "xlsx")
    assert x["s3_url"].startswith(config.S3 + "InventoryList/") and x["s3_url"].endswith("/INDIGO/excel/1.01.xlsx")
    assert x["fy"] == "2017-18" and "SCHEDULED DOMESTIC" in x["title"]


def test_year_page_discovery_and_fy_labels():
    idx = (FX / "html/yearly_index.html").read_text()
    pages = discover.year_pages(idx)
    fys = {p["fy"] for p in pages}
    assert {"2017-18", "2016-17"} <= fys
    assert discover.fy_label("Air Transport Statistics for the year 2017-2018") == "2017-18"
    assert discover.fy_label("nothing here") is None


@pytest.mark.parametrize("title,ext,expected", [
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF INDIGO ON SCHEDULED DOMESTIC SERVICES DURING 2017-18.", "xlsx", "domestic"),
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF X ON SCHEDULED INTERNATIONAL SERVICES DURING 2017-18.", "xlsx", "international"),
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF X ON NON SCHEDULED DOMESTIC SERVICES DURING 2017-18.", "xlsx", None),
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF X ON NON-SCHEDULED DOMESTIC SERVICES", "xlsx", None),
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF X ON TOTAL SCHEDULED (INTERNATIONAL+DOMESTIC) SERVICES", "xlsx", None),
    ("MONTHLY TRAFFIC AND OPERATING STATISTICS OF X ON SCHEDULED DOMESTIC SERVICES", "pdf", None),
])
def test_registry_selects_only_scheduled_domestic_and_international_xlsx(title, ext, expected):
    ds = dataset_for(title, ext)
    assert (ds["segment"] if ds else None) == expected


@pytest.mark.parametrize("file,fy", [("indigo_2017-18_1.01.xlsx", "2017-18"), ("indigo_2018-19_1.01_datecells.xlsx", "2018-19")])
def test_parser_handles_both_layouts_and_reconciles(file, fy):
    months, annual = monthly.read(FX / file, fy)
    assert list(months.month) == monthly.MONTHS and len(months) == 12
    assert monthly.validate(months, annual) == []


def test_parser_known_value_indigo_2017_18():
    months, _ = monthly.read(FX / "indigo_2017-18_1.01.xlsx", "2017-18")
    assert months.pax.sum() == 48955983 and months.departures.sum() == 326233


def test_validator_catches_corruption():
    months, annual = monthly.read(FX / "indigo_2017-18_1.01.xlsx", "2017-18")
    months.loc[3, "pax"] += 1_000_000
    assert any("pax" in p for p in monthly.validate(months, annual))


def test_layout_error_when_not_a_monthly_sheet(tmp_path):
    p = tmp_path / "bad.xlsx"
    pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(p, index=False)
    with pytest.raises(monthly.LayoutError):
        monthly.read(p, "2017-18")


def test_entity_aliases_merge_excel_suffix():
    assert canonical("AIR ASIA excel") == canonical("AIR ASIA ") == "AIR ASIA"
    assert display("AIR ASIA") == "AirAsia India"


def test_manifest_upsert_is_idempotent_and_keeps_state():
    rec = {"s3_url": "https://x/a.xlsx", "portal_path": "a", "section": "s", "fy": "2017-18", "entity": "E", "table_id": "1.01", "title": "t", "ext": "xlsx", "source_page": "p"}
    df, st = manifest.upsert(manifest.empty(), [rec, rec])      # duplicate discovery in one run
    assert st["new"] == 1 and len(df) == 1
    df.loc[0, ["status", "content_sha256"]] = ["downloaded", "abc"]
    df, st = manifest.upsert(df, [rec])
    assert st["new"] == 0 and st["seen_again"] == 1 and df.loc[0, "status"] == "downloaded" and df.loc[0, "content_sha256"] == "abc"


def _parsed(entity, sha, first_seen, pax=100.0):
    cols = {c: 1.0 for c in build.FACT_COLS}
    cols["pax"] = pax
    return pd.DataFrame([{"entity": entity, "fy": "2017-18", "segment": "domestic", "month": "APR", **cols, "file_sha256": sha,
                          "first_seen": first_seen, "last_modified": None, "airline_key": "air_asia"}])


def test_row_dedupe_newest_wins_and_conflicts_are_logged():
    same = pd.concat([_parsed("AIR ASIA", "a" * 64, "2026-01-01"), _parsed("AIR ASIA excel", "b" * 64, "2026-02-01")])
    out, conf = build.dedupe(same)
    assert len(out) == 1 and conf.empty                          # identical values -> silent dedupe
    diff = pd.concat([_parsed("AIR ASIA", "a" * 64, "2026-01-01", 100), _parsed("AIR ASIA excel", "b" * 64, "2026-02-01", 90)])
    out, conf = build.dedupe(diff)
    assert len(out) == 1 and out.pax.iloc[0] == 90 and len(conf) == 2   # newest kept, disagreement recorded


def test_published_bundle_is_consistent():
    ver = (config.SITE_DATA / "latest.json")
    if not ver.exists():
        pytest.skip("no published data")
    import json
    d = config.SITE_DATA / json.loads(ver.read_text())["version"]
    fact = pd.read_parquet(d / "fact_airline_monthly.parquet")
    air, per, seg = (pd.read_parquet(d / f"{t}.parquet") for t in ("dim_airline", "dim_period", "dim_segment"))
    assert not fact.duplicated(["airline_key", "period_key", "segment_key"]).any()          # natural key unique
    assert set(fact.airline_key) <= set(air.airline_key) and set(fact.period_key) <= set(per.period_key)
    assert set(fact.segment_key) <= set(seg.segment_key)
    assert air.airline.is_unique
    assert "AirAsia India" in set(air.airline) and not any(a.endswith("excel") for a in air.airline)
    # aggregates are flagged so they are never summed with airlines
    assert set(air[air.airline_type == "Aggregate"].airline) == {"All scheduled Indian airlines", "Scheduled national airlines", "Scheduled private airlines"}


def test_sum_of_airlines_close_to_all_scheduled_total():
    """Cross-table check: individual airlines should explain >=95% of the DGCA 'all scheduled' roll-up."""
    ver = config.SITE_DATA / "latest.json"
    if not ver.exists():
        pytest.skip("no published data")
    import json
    d = config.SITE_DATA / json.loads(ver.read_text())["version"]
    f = pd.read_parquet(d / "fact_airline_monthly.parquet").merge(pd.read_parquet(d / "dim_airline.parquet"), on="airline_key").merge(pd.read_parquet(d / "dim_period.parquet"), on="period_key")
    for (fy, seg), g in f.groupby(["fy", "segment_key"]):
        if fy < "2016-17":
            continue  # earlier roll-ups use a different airline set
        tot = g[g.airline == "All scheduled Indian airlines"].pax.sum()
        parts = g[g.airline_type == "Airline"].pax.sum()
        assert tot and 0.95 <= parts / tot <= 1.05, (fy, seg, parts / tot)
