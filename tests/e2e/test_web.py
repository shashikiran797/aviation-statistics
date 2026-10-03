"""End-to-end browser tests (Playwright + real DuckDB-WASM). Expected numbers are computed independently from
data/interim parquet with pandas, never through the SQL path under test. Screenshots land in docs/screenshots/."""
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8791
URL = f"http://localhost:{PORT}/"
AGG = {"ALL SCHEDULED INDIAN AIRLINES", "SCHEDULED NATIONAL AIRLINES", "SCHEDULED PRIVATE AIRLINES"}


@pytest.fixture(scope="module")
def interim():
    """Expected numbers, computed with plain pandas straight from the published parquet (not via the SQL/WASM path under test).
    Pipeline correctness against the raw source files is covered separately in tests/test_pipeline.py."""
    d = next(p for p in (ROOT / "web/data").iterdir() if p.is_dir())
    f = pd.read_parquet(d / "fact_airline_monthly.parquet")
    for t, k in (("dim_airline", "airline_key"), ("dim_period", "period_key"), ("dim_segment", "segment_key")):
        f = f.merge(pd.read_parquet(d / f"{t}.parquet"), on=k)
    return f[f.airline_type == "Airline"]


@pytest.fixture(scope="module")
def nfy(interim):
    return interim.fy.nunique()


@pytest.fixture(scope="module")
def server():
    p = subprocess.Popen([sys.executable, str(ROOT / "scripts/serve.py"), str(PORT)])
    time.sleep(1)
    yield
    p.terminate()


@pytest.fixture(scope="module")
def browser(server):
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def boot(pg, url=URL):
    """Open the app. The libraries come from a CDN, so one stalled load is retried once (a user would simply reload)."""
    for attempt in (1, 2):
        pg.goto(url)
        try:
            pg.wait_for_selector("body[data-ready='1']", timeout=45000)
            return
        except Exception:
            if attempt == 2 or "Failed to start" in pg.inner_text("#data-meta"):
                raise                                                   # a real startup failure is never retried away


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and pg.errors.append(m.text))
    boot(pg)
    yield pg
    assert not pg.errors, pg.errors
    ctx.close()


def num(text):
    t = text.strip().replace(",", "")
    if re.fullmatch(r"[\d.]+", t):
        return float(t)
    m = re.match(r"([\d.]+)([KMB%]?)", t)
    return float(m.group(1)) * {"": 1, "%": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]


def kpi(pg, label):
    """Exact value (tooltip attribute carries the unrounded number, e.g. '4,569,785 flights')."""
    t = pg.locator(".kpi", has_text=label).get_attribute("title")
    return t.split(" ")[0].replace(",", "")


def shot(pg, name, **kw):
    pg.screenshot(path=str(SHOTS / f"{name}.png"), **kw)


def test_boot_renders_all_starter_widgets_and_kpis_match_pandas(page, interim, nfy):
    assert page.locator(".card").count() == 5
    assert page.locator(".card svg").count() >= 3
    assert not page.locator(".card .error").count()
    assert num(kpi(page, "Passengers carried")) == pytest.approx(interim.pax.sum(), rel=0.001)
    assert num(kpi(page, "Departures")) == pytest.approx(interim.departures.sum(), rel=0.001)
    plf = 100 * interim.pax_km_million.sum() / interim.ask_million.sum()
    assert num(kpi(page, "Passenger load factor")) == pytest.approx(plf, abs=0.06)
    # 13 fiscal-year labels must not collide: rotated ticks, each label has its own non-overlapping box
    boxes = page.locator("#w-pax-fy svg [aria-label='x-axis tick label'] text").evaluate_all("els => els.map(e => e.getBoundingClientRect()).map(r => [r.left, r.right, r.top, r.bottom])")
    assert len(boxes) == nfy
    rotated = page.locator("#w-pax-fy svg [aria-label='x-axis tick label'] text").first.get_attribute("transform")
    assert rotated and "rotate" in rotated
    shot(page, "01-overview")


def test_wasm_matches_native_duckdb_on_every_vector(page):
    import duckdb
    vec = json.loads((ROOT / "tests/vectors/spec_cases.json").read_text())["cases"]
    d = sorted((ROOT / "web/data").glob("*/"))[0] if False else next(p for p in (ROOT / "web/data").iterdir() if p.is_dir())
    con = duckdb.connect()
    for f in sorted(d.glob("*.parquet")):
        if f.stem.startswith(("fact_", "dim_")):
            con.execute(f"create view {f.stem} as select * from '{f}'")
    n = 0
    for c in vec:
        if "sql" not in c:
            continue
        native = con.execute(c["sql"]).fetchall()
        res = page.evaluate("async (sql) => (await window.__dgca.query(sql)).rows.map(r => Object.values(r))", c["sql"])
        assert len(res) == len(native), c["name"]
        for a, b in zip(res, native):
            for x, y in zip(a, b):
                if isinstance(y, (int, float)):
                    assert x == pytest.approx(y, rel=1e-9), c["name"]
        n += 1
    assert n >= 12


def test_global_filter_changes_numbers_correctly(page, interim):
    page.click("#f-fy")
    page.locator(".pop label", has_text="2017-18").click()
    page.keyboard.press("Escape")
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("document.querySelector('#f-fy b').textContent.includes('2017-18')")
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    x = interim[interim.fy == "2017-18"]
    assert num(kpi(page, "Passengers carried")) == pytest.approx(x.pax.sum(), rel=0.001)
    # segment filter on top
    page.click("#f-segment")
    page.locator(".pop label", has_text="Domestic").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    xd = x[x.segment == "Domestic"]
    assert num(kpi(page, "Passengers carried")) == pytest.approx(xd.pax.sum(), rel=0.001)
    shot(page, "02-filtered-fy-domestic")
    page.click("text=Clear all")
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    assert num(kpi(page, "Passengers carried")) == pytest.approx(interim.pax.sum(), rel=0.001)


def test_aggregates_are_excluded_by_default_and_selectable(page):
    assert page.locator("#f-airline_type b").inner_text().startswith("Airlines")
    page.click("#f-airline_type")
    page.locator(".pop label", has_text="Aggregate").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    # aggregate pax (all scheduled + national + private) is far larger than airlines alone: nothing silently mixed
    assert num(kpi(page, "Passengers carried")) > 1e9


def test_line_tooltip_lists_all_series_and_bar_click_crossfilters(page):
    line = page.locator("#w-pax-month svg").first
    box = line.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)   # ~2017: both segments flying (scheduled int'l was suspended Mar 2020 - Mar 2022)
    tip = page.locator("#tip")
    expect(tip).to_be_visible()
    txt = tip.inner_text()
    assert "Domestic" in txt and "International" in txt
    shot(page, "03-line-tooltip")
    page.mouse.move(5, 5)
    # click airline bar => global airline filter
    bars = page.locator("#w-top-airlines g[aria-label='bar'] > *")
    assert bars.count() == 12
    bars.first.click()
    page.wait_for_function("document.querySelector('#f-airline b').textContent.includes('IndiGo')")
    shot(page, "04-crossfilter-indigo")


def test_widget_table_toggle_and_csv_download(page, interim, nfy):
    page.locator("#w-pax-fy button[title='Toggle table view']").click()
    n = interim.groupby(["fy", "segment"]).ngroups
    assert page.locator("#w-pax-fy table.t tbody tr").count() == n
    with page.expect_download() as dl:
        page.locator("#w-pax-fy button[title='Download CSV']").click()
    csv = Path(dl.value.path()).read_text().splitlines()
    assert csv[0] == "fy,segment,pax" and len(csv) == n + 1
    page.locator("#w-pax-fy button[title='View SQL']").click()
    sql = page.locator("#modal pre").first.text_content()
    assert sql.startswith("SELECT dim_period.fy") and "airline_type IN ('Airline')" in sql
    shot(page, "05-view-sql")


def test_builder_click_to_add_preview_save_forks_starter_and_persists(page):
    page.click("#btn-new")
    expect(page.locator("#builder")).to_be_visible()
    page.locator("#b-meas .chip", has_text="Passenger load factor").click()
    page.locator("#s-measures .chip", has_text="Passengers carried").locator(".x").click()
    page.locator("#b-dims .chip", has_text="Fiscal year").click()
    page.locator("#b-dims .chip", has_text="Segment").click()
    page.wait_for_selector("#b-preview svg")
    assert "dim_period.fy" in page.text_content("#b-sql") and "dim_segment.segment" in page.text_content("#b-sql")
    page.fill("#b-title", "My load factor")
    # add a filter on airline
    page.click("#b-addfilter")
    page.locator("#s-filters select").first.select_option("airline")
    page.locator("#s-filters button", has_text="choose values").click()
    page.locator(".pop label", has_text="IndiGo").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('IndiGo')")
    shot(page, "06-builder")
    page.click("#b-save")
    page.wait_for_function("document.querySelectorAll('.card').length === 6")
    assert page.locator("#dash-select option:checked").inner_text().endswith("(my copy)")
    assert page.locator(".card h3", has_text="My load factor").count() == 1
    page.reload()
    page.wait_for_selector("body[data-ready='1']", timeout=90000)
    page.select_option("#dash-select", label="Air traffic overview (my copy)")
    page.wait_for_function("document.querySelectorAll('.card').length === 6")
    assert page.locator(".card h3", has_text="My load factor").count() == 1


def test_builder_drag_and_drop(page):
    page.click("#btn-new")
    chip = page.locator("#b-dims .chip", has_text="Airline").first
    page.drag_and_drop("#b-dims .chip >> text=Airline", "#s-dimensions")
    assert page.locator("#s-dimensions .chip", has_text="Airline").count() == 1
    page.fill("#b-topn", "3")
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('LIMIT 3')")
    page.wait_for_selector("#b-preview svg")


def test_invalid_builder_state_shows_error_not_crash(page):
    page.click("#btn-new")
    page.locator("#s-measures .chip .x").first.click()  # nothing selected
    expect(page.locator("#b-error")).to_be_visible()
    assert "at least one" in page.inner_text("#b-error")


def test_share_link_roundtrip_and_export_import(page, browser):
    page.click("#btn-share")
    page.wait_for_function("location.hash.startsWith('#d=')")
    url = page.url
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    p2 = ctx.new_page()
    p2.goto(url)
    p2.wait_for_selector("body[data-ready='1']", timeout=90000)
    assert p2.locator(".card").count() == 5
    ctx.close()
    page.click("#btn-menu")
    with page.expect_download() as dl:
        page.click("#menu >> text=Export JSON")
    exported = json.loads(Path(dl.value.path()).read_text())
    assert len(exported["widgets"]) == 5
    before = page.locator("#dash-select option").count()
    page.click("#btn-menu")
    with page.expect_file_chooser() as fc:
        page.click("#menu >> text=Import JSON")
    fc.value.set_files(dl.value.path())
    page.wait_for_function(f"document.querySelectorAll('#dash-select option').length === {before + 1}")


def test_sql_console_allows_select_and_blocks_everything_else(page, nfy):
    page.click("#btn-menu")
    page.click("#menu >> text=Advanced")
    page.click("text=Run (read-only)")
    page.wait_for_selector("#modal table.t")
    assert page.locator("#modal table.t tbody tr").count() == nfy
    for bad in ["DROP TABLE dim_airline", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('/etc/passwd')", "COPY dim_airline TO 'x.csv'"]:
        page.fill("#modal textarea", bad)
        page.click("text=Run (read-only)")
        expect(page.locator("#modal .error")).to_contain_text("Only a single SELECT")
    shot(page, "07-sql-console")


def test_sources_modal(page):
    page.click("#btn-menu")
    page.click("#menu >> text=Data sources")
    page.wait_for_selector("#modal table.t")
    assert "Excel files parsed" in page.inner_text("#modal") and "city-pair" in page.inner_text("#modal")
    txt = page.inner_text("#modal")
    assert page.locator("#modal table.t tbody tr").count() >= 8                   # 5 fiscal years + operator series + city pairs + international
    for label in ("operator monthly series", "domestic city-pair traffic", "international traffic, quarterly tables"):
        assert label in txt, label
    shot(page, "08-sources")


def test_ai_panel_unavailable_state_gives_instructions(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("delete globalThis.LanguageModel; Object.defineProperty(navigator, 'gpu', {value: undefined});")
    page = ctx.new_page()
    page.goto(URL)
    page.wait_for_selector("body[data-ready='1']", timeout=90000)
    page.click("#btn-ai")
    expect(page.locator("#tier-chrome")).to_contain_text("chrome://flags")
    assert page.locator("#ai-chrome-enable").is_disabled()          # headless chromium has no Prompt API
    expect(page.locator("#tier-webllm")).to_contain_text("no WebGPU")
    assert page.locator("#ai-webllm-enable").is_disabled()
    page.fill("#ai-q", "passengers by year")
    page.click("#ai-ask")
    expect(page.locator("#ai-out .error")).to_contain_text("Enable that backend first")
    shot(page, "09-ai-setup")
    ctx.close()


FAKE_LM = """
globalThis.LanguageModel = {
  _log: [],
  async availability() { return 'downloadable'; },
  async create(opts) {
    LanguageModel._log.push({create: opts});
    if (opts.monitor) { const t = new EventTarget(); opts.monitor(t); const e = new Event('downloadprogress'); e.loaded = 1; t.dispatchEvent(e); }
    return { async prompt(q, o) { LanguageModel._log.push({prompt: q, constraint: o && o.responseConstraint});
      return JSON.stringify({fact:'airline_monthly', dimensions:['fy'], measures:['pax'], filters:[], top:null, sort:null, limit:null, viz:'auto'}); }, destroy() {} };
  } };
"""


def test_chrome_prompt_api_path_with_stubbed_model(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script(FAKE_LM)
    page = ctx.new_page()
    page.goto(URL)
    page.wait_for_selector("body[data-ready='1']", timeout=90000)
    page.click("#btn-ai")
    assert not page.locator("#ai-chrome-enable").is_disabled()
    expect(page.locator("#tier-chrome")).to_contain_text("downloadable")
    page.click("#ai-chrome-enable")
    expect(page.locator("#ai-chrome-enable")).to_have_text("Enabled ✓")
    page.fill("#ai-q", "passengers by fiscal year"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    page.wait_for_selector("#b-preview svg")
    log = page.evaluate("LanguageModel._log")
    prompt = next(x for x in log if "prompt" in x)
    assert prompt["prompt"] == "passengers by fiscal year"
    assert "airline_monthly" in json.dumps(prompt["constraint"])   # structured-output schema is passed
    sysmsg = [x for x in log if "create" in x and x["create"].get("initialPrompts")][0]["create"]["initialPrompts"][0]
    assert sysmsg["role"] == "system" and "pax_load_factor" in sysmsg["content"] and "IndiGo" in sysmsg["content"]
    ctx.close()


def test_ai_pipeline_with_mock_model_valid_repair_and_failure(page):
    spec = {"fact": "airline_monthly", "dimensions": ["month", "airline"], "measures": ["pax"],
            "filters": [{"dim": "fy", "op": "in", "values": ["2018-19"]}], "top": {"dim": "airline", "measure": "pax", "n": 5}, "sort": None, "limit": None, "viz": "line"}
    # 1) valid first try
    page.evaluate("s => { window.__DGCA_AI_MOCK = async () => '```json\\n' + JSON.stringify(s) + '\\n```'; }", spec)
    page.click("#btn-ai"); page.fill("#ai-q", "Top 5 airlines in 2018-19 by month"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    page.wait_for_selector("#b-preview svg")
    assert "LIMIT 5" in page.text_content("#b-sql") and "2018-19" in page.text_content("#b-sql")
    shot(page, "10-ai-generated-spec")
    page.click("#b-cancel")
    # 2) invalid first, repaired second (model sees the compiler error)
    page.evaluate("""s => { let n = 0; window.__DGCA_AI_MOCK = async (sys, msgs) => {
        n++; window.__lastMsgs = msgs; return n === 1 ? JSON.stringify({fact:'airline_monthly', dimensions:['airline_name'], measures:['pax']}) : JSON.stringify(s); }; }""", spec)
    page.click("#btn-ai"); page.fill("#ai-q", "top airlines"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    assert "unknown dimension" in page.evaluate("window.__lastMsgs.at(-1).content")
    page.click("#b-cancel")
    # 3) model never valid => clear error, no crash
    page.evaluate("window.__DGCA_AI_MOCK = async () => 'sorry I cannot'")
    page.click("#btn-ai"); page.fill("#ai-q", "x"); page.click("#ai-ask")
    expect(page.locator("#ai-out .error")).to_contain_text("did not produce a valid chart spec")


def test_ai_cannot_smuggle_sql_through_spec(page):
    evil = {"fact": "airline_monthly", "dimensions": ["airline"], "measures": ["pax"],
            "filters": [{"dim": "airline", "op": "in", "values": ["x'); DROP VIEW dim_airline; --"]}]}
    page.evaluate("s => { window.__DGCA_AI_MOCK = async () => JSON.stringify(s); }", evil)
    page.click("#btn-ai"); page.fill("#ai-q", "evil"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    page.wait_for_function("document.querySelector('#b-sql').textContent.length > 20")
    assert "x''); DROP VIEW" in page.text_content("#b-sql")
    assert page.evaluate("async () => (await window.__dgca.query('select count(*) n from dim_airline')).rows[0].n") >= 30


def test_dark_theme_and_other_dashboards(page):
    page.click("#btn-theme")
    page.wait_for_function("document.documentElement.dataset.theme")
    page.select_option("#dash-select", label="★ Airline comparison")
    page.wait_for_function("document.querySelectorAll('.card').length === 4 && !document.querySelector('.card-body.loading')")
    assert page.locator("#w-heat g[aria-label='cell'] > *").count() >= 40
    shot(page, "11-airline-comparison-dark", full_page=True)
    page.select_option("#dash-select", label="★ Load factor trends")
    page.wait_for_function("document.querySelectorAll('.card').length === 4 && !document.querySelector('.card-body.loading')")
    shot(page, "12-load-factor-dark", full_page=True)


def test_mobile_layout_has_no_horizontal_scroll(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector("body[data-ready='1']", timeout=90000)
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    pg.screenshot(path=str(SHOTS / "13-mobile.png"), full_page=True)
    ctx.close()


def _summary_count(pg):
    return int(re.match(r"([\d,]+)", pg.inner_text("#cat-summary")).group(1).replace(",", ""))


def test_catalog_search_filters_and_links(page):
    page.click("#v-cat")
    page.wait_for_selector("#cat-results .cat-row")
    total = _summary_count(page)
    assert total > 100 and page.locator("#cat-sections button").count() > 3
    assert page.locator("#view-dash").is_hidden() and page.locator("#btn-new").is_hidden() and page.locator("#filters").is_hidden()
    shot(page, "14-catalog")
    page.fill("#cat-q", "aerodrome")
    page.wait_for_function(f"document.querySelector('#cat-summary').textContent.includes('aerodrome')")
    narrowed = _summary_count(page)
    assert 0 < narrowed < total
    rows = page.locator("#cat-results .cat-row")
    assert "aerodrome" in rows.first.inner_text().lower()
    href = rows.locator("a").first.get_attribute("href")        # first row with a direct file link (some hits are dynamic pages)
    assert href.startswith("https://public-prd-dgca.s3")
    page.click("#ct-pdf")
    page.wait_for_function("document.querySelector('#ct-pdf').classList.contains('on')")
    assert _summary_count(page) <= narrowed
    page.fill("#cat-q", "zzzz-no-such-document")
    page.wait_for_function("document.querySelector('#cat-summary').textContent.startsWith('0 ')")
    assert page.locator("#cat-results .cat-row").count() == 0
    page.fill("#cat-q", "x'); DROP VIEW site_files; --")        # search box is escaped, not executed
    page.wait_for_function("document.querySelector('#cat-summary').textContent.startsWith('0 ')")
    page.fill("#cat-q", "")
    page.click("#ct-all")
    page.wait_for_function("document.querySelector('#cat-results .cat-row')")
    assert _summary_count(page) == total
    page.click("#v-dash")
    page.wait_for_function("document.querySelectorAll('.card svg').length >= 3")


def test_catalog_section_filter_and_paging(page):
    page.click("#v-cat")
    page.wait_for_selector("#cat-results .cat-row")
    total = _summary_count(page)
    page.locator("#cat-sections button").nth(2).click()
    page.wait_for_function(f"document.querySelector('#cat-summary').textContent.includes(' in ')")
    assert _summary_count(page) < total
    page.locator("#cat-sections button").first.click()
    page.wait_for_function(f"!document.querySelector('#cat-summary').textContent.includes(' in ')")
    nxt = page.locator(".pager button", has_text="Next")
    assert nxt.is_enabled()
    first = page.locator("#cat-results .cat-row").first.inner_text()
    nxt.click()
    page.wait_for_function("document.querySelector('.pager span').textContent.startsWith('Page 2')")
    assert page.locator("#cat-results .cat-row").first.inner_text() != first


def test_catalog_extracted_table_viewer(page):
    page.click("#v-cat")
    page.wait_for_selector("#ct-tables")
    page.click("#ct-tables")
    page.wait_for_selector("#cat-results .cat-row button.lk")
    n = _summary_count(page)
    assert n >= 5
    page.locator("#cat-results .cat-row button.lk").first.click()
    page.wait_for_selector("#modal table.t tbody tr")
    rows = page.locator("#modal table.t tbody tr").count()
    assert rows >= 2
    cell = page.locator("#modal table.t tbody tr").nth(0).locator("td").nth(1).inner_text().strip()
    shot(page, "15-extracted-table")
    word = next((w for w in re.findall(r"[A-Za-z]{5,}", cell)), None)
    if word:
        page.fill("#modal input[type=search]", word.lower())
        page.wait_for_timeout(600)
        assert 1 <= page.locator("#modal table.t tbody tr").count() <= rows
    with page.expect_download() as dl:
        page.click("#modal >> text=Download CSV")
    assert len(Path(dl.value.path()).read_text().splitlines()) >= 2


def test_missing_months_break_the_line_instead_of_interpolating(page):
    page.select_option("#dash-select", label="★ Load factor trends")
    page.wait_for_function("document.querySelectorAll('.card').length === 4 && !document.querySelector('.card-body.loading')")
    # international scheduled flights were suspended Mar 2020 - Mar 2022: that series must have a break (>= 2 subpaths)
    paths = page.locator("#w-plf-seg svg [aria-label='line'] path").evaluate_all("els => els.map(e => (e.getAttribute('d') || '').split('M').length - 1)")
    assert max(paths) >= 1 and sorted(paths)[0] >= 1
    assert any(p >= 2 for p in paths), paths
    # capacity-by-fiscal-year keeps chronological order top to bottom (ordered dimension is never value-sorted)
    labels = page.locator("#w-cap svg [aria-label='x-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert len(labels) >= 12 and labels == sorted(labels, key=lambda t: t.replace(" (YTD)", "~")), labels


# ================================================================ routes (city-pair) dataset
@pytest.fixture(scope="module")
def routes():
    d = next(p for p in (ROOT / "web/data").iterdir() if p.is_dir())
    f = pd.read_parquet(d / "fact_route_monthly.parquet")
    for t, k in (("dim_period", "period_key"), ("dim_route", "route_key")):
        f = f.merge(pd.read_parquet(d / f"{t}.parquet"), on=k)
    pl = pd.read_parquet(d / "dim_place.parquet").set_index("place_key")
    f["origin"] = f.origin_key.map(pl.place)
    f["destination"] = f.dest_key.map(pl.place)
    return f


def goto_routes(pg):
    pg.select_option("#dash-select", label="★ Routes & airports")
    pg.wait_for_function("document.querySelectorAll('.card').length === 7 && !document.querySelector('.card-body.loading')")


def test_routes_dashboard_kpis_and_top_routes_match_pandas(page, routes):
    goto_routes(page)
    assert num(kpi(page, "Route passengers")) == pytest.approx(routes.pax.sum(), rel=1e-9)
    assert num(kpi(page, "Route freight")) == pytest.approx(routes.freight_t.sum(), rel=1e-6)     # NaN (pre-2018) is skipped, not zero
    top = routes.groupby("route").pax.sum().sort_values(ascending=False).head(15)
    labels = page.locator("#w-rt-top svg [aria-label='y-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert labels == list(top.index)                                       # same 15 routes, same order, both directions summed
    assert "individual airlines" not in page.inner_text("#w-rt-top")        # airline-only default filter must not leak onto route widgets
    shot(page, "16-routes-dashboard")


def test_filter_bar_follows_the_dashboards_facts(page):
    assert page.locator("#f-airline").count() == 1 and page.locator("#f-origin").count() == 0
    goto_routes(page)
    assert page.locator("#f-airline").count() == 0 and page.locator("#f-segment").count() == 0
    assert all(page.locator(f"#f-{d}").count() == 1 for d in ("fy", "origin", "destination", "route"))
    page.select_option("#dash-select", label="★ Airline comparison")
    page.wait_for_function("document.querySelector('#f-airline')")
    assert page.locator("#f-route").count() == 0


def test_searchable_picker_filters_by_route_and_matches_pandas(page, routes):
    goto_routes(page)
    page.click("#f-route")
    search = page.locator(".pop input[type=search]")
    expect(search).to_be_visible()                                         # 1,699 routes need search
    search.fill("delhi – mumbai")
    page.wait_for_function("[...document.querySelectorAll('.pop label')].filter(l => !l.hidden).length === 1")
    shot(page, "17-route-picker-search")
    page.locator(".pop label:not([hidden])").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-route b').textContent.includes('Mumbai')")
    x = routes[routes.route == "Delhi – Mumbai"]
    assert num(kpi(page, "Route passengers")) == pytest.approx(x.pax.sum(), rel=1e-9)
    # the same route in one direction only: origin filter on top
    page.click("#f-origin")
    page.locator(".pop input[type=search]").fill("delhi")
    page.locator(".pop label:not([hidden])", has_text="Delhi").first.click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-origin b').textContent.includes('Delhi')")
    assert num(kpi(page, "Route passengers")) == pytest.approx(x[x.origin == "Delhi"].pax.sum(), rel=1e-9)
    assert x[x.origin == "Delhi"].pax.sum() < x.pax.sum()
    shot(page, "18-route-filtered")


def test_fiscal_year_filter_applies_across_facts(page, routes):
    goto_routes(page)
    page.click("#f-fy")
    page.locator(".pop label", has_text="2018-19").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-fy b').textContent.includes('2018-19')")
    assert num(kpi(page, "Route passengers")) == pytest.approx(routes[routes.fy == "2018-19"].pax.sum(), rel=1e-9)
    page.select_option("#dash-select", label="★ Air traffic overview")        # the same global filter still scopes the airline fact
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    assert 130e6 < num(kpi(page, "Passengers carried")) < 175e6


def test_route_bar_click_crossfilters_and_heatmap_renders(page):
    goto_routes(page)
    page.locator("#w-rt-top g[aria-label='bar'] > *").first.click()
    page.wait_for_function("document.querySelector('#f-route b').textContent.includes('–')")
    page.click("text=Clear all")
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    assert page.locator("#w-rt-heat g[aria-label='cell'] > *").count() >= 100


def test_builder_with_route_fact_uses_role_playing_joins(page, routes):
    page.click("#btn-new")
    page.select_option("#b-fact", label="Domestic routes (city pairs)")
    page.wait_for_function("document.querySelector('#s-measures .chip')")
    assert page.locator("#s-measures .chip", has_text="Route passengers").count() == 1      # sensible default measure
    page.locator("#b-dims .chip", has_text="Origin").first.click()
    page.locator("#b-dims .chip", has_text="Destination").first.click()
    page.fill("#b-topn", "5")
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('dim_place AS dim_destination')")
    sql = page.text_content("#b-sql")
    assert "JOIN dim_place AS dim_origin" in sql and "JOIN dim_place AS dim_destination" in sql
    assert "airline_type" not in sql                                       # no airline default filter on a route fact
    page.wait_for_selector("#b-preview svg, #b-preview table")
    got = page.evaluate("async (s) => (await window.__dgca.query(s)).rows.length", sql)
    assert got > 0
    exp = routes.groupby("origin").pax.sum().nlargest(5).index
    shot(page, "19-builder-route-fact")


def test_ai_picks_the_route_fact_and_prompt_describes_both_facts(page):
    spec = {"fact": "route_monthly", "dimensions": ["period"], "measures": ["route_pax"],
            "filters": [{"dim": "origin", "op": "in", "values": ["Delhi"]}, {"dim": "destination", "op": "in", "values": ["Mumbai"]}],
            "top": None, "sort": None, "limit": None, "viz": "line"}
    page.evaluate("s => { window.__seen = null; window.__DGCA_AI_MOCK = async (sys) => { window.__seen = sys; return JSON.stringify(s); }; }", spec)
    page.click("#btn-ai"); page.fill("#ai-q", "monthly passengers from Delhi to Mumbai"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('dim_origin')")
    assert page.text_content("#b-fact-desc").startswith("Passengers, freight and mail carried between")
    sysmsg = page.evaluate("window.__seen")
    for needle in ('Fact "airline_monthly"', 'Fact "route_monthly"', "route_pax", "pax_load_factor", "IndiGo", "Busiest 10 routes"):
        assert needle in sysmsg, needle
    shot(page, "20-ai-route-question")


def test_ai_route_spec_for_an_airline_dimension_is_repaired(page):
    bad = {"fact": "route_monthly", "dimensions": ["airline"], "measures": ["route_pax"]}
    good = {"fact": "route_monthly", "dimensions": ["route"], "measures": ["route_pax"], "sort": {"by": "route_pax", "dir": "desc"}, "limit": 10}
    page.evaluate("([b, g]) => { let n = 0; window.__DGCA_AI_MOCK = async (s, m) => { n++; window.__last = m.at(-1).content; return JSON.stringify(n === 1 ? b : g); }; }", [bad, good])
    page.click("#btn-ai"); page.fill("#ai-q", "busiest routes"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    last = page.evaluate("window.__last")
    assert "unknown dimension airline for route_monthly" in last and "valid dimensions: origin" in last   # the compiler's fact-specific error reaches the model


def test_sources_modal_documents_data_quality_and_known_gaps(page):
    page.click("#btn-menu")
    page.click("#menu >> text=Data sources")
    page.wait_for_selector("#dq")
    txt = page.inner_text("#dq")
    assert "125 of 134 months agree" in txt and "0 unexplained" in txt
    assert "Jet Airways" in txt and "2015" in txt
    assert "May 2021" in txt and "May 2024" in txt
    shot(page, "21-data-quality")


def test_routes_dashboard_dark_and_mobile(page, browser):
    goto_routes(page)
    page.click("#btn-theme")
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    shot(page, "22-routes-dark", full_page=True)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector("body[data-ready='1']", timeout=90000)
    pg.select_option("#dash-select", label="★ Routes & airports")
    pg.wait_for_function("document.querySelectorAll('.card').length === 7 && !document.querySelector('.card-body.loading')")
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    pg.screenshot(path=str(SHOTS / "23-routes-mobile.png"), full_page=True)
    ctx.close()


# ================================================================ international (quarterly) datasets
@pytest.fixture(scope="module")
def intl():
    d = next(p for p in (ROOT / "web/data").iterdir() if p.is_dir())
    R = lambda t: pd.read_parquet(d / f"{t}.parquet")
    a = R("fact_intl_airline_monthly").merge(R("dim_carrier"), on="carrier_key").merge(R("dim_direction"), on="direction_key").merge(R("dim_period"), on="period_key")
    c = R("fact_intl_country_quarterly").merge(R("dim_country"), on="country_key").merge(R("dim_direction"), on="direction_key").merge(R("dim_period"), on="period_key")
    pl = R("dim_place").set_index("place_key")
    p = R("fact_intl_citypair_quarterly").merge(R("dim_route"), on="route_key").merge(R("dim_period"), on="period_key")
    p["origin"] = p.origin_key.map(pl.place)
    return {"air": a, "country": c, "pairs": p}


def goto_intl(pg):
    pg.select_option("#dash-select", label="★ International traffic")
    pg.wait_for_function("document.querySelectorAll('.card').length === 9 && !document.querySelector('.card-body.loading')")


def test_international_dashboard_renders_and_kpis_match_pandas(page, intl):
    goto_intl(page)
    a = intl["air"]
    assert not page.locator(".card .error").count()
    assert num(kpi(page, "International passengers")) == pytest.approx(a.pax.sum(), rel=1e-9)
    assert num(kpi(page, "International freight")) == pytest.approx(a.freight_t.sum(), rel=1e-6)
    top = a.groupby("carrier").pax.sum().sort_values(ascending=False).head(15)
    labels = page.locator("#w-in-carriers svg [aria-label='y-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert labels == list(top.index)
    top = intl["country"].groupby("country").pax.sum().sort_values(ascending=False).head(15)
    labels = page.locator("#w-in-countries svg [aria-label='y-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert labels == list(top.index) and labels[0] == "United Arab Emirates"
    top = intl["pairs"].groupby("route").pax.sum().sort_values(ascending=False).head(15)
    labels = page.locator("#w-in-routes svg [aria-label='y-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert labels == list(top.index)
    assert page.locator("#w-in-heat g[aria-label='cell'] > *").count() >= 100
    assert "individual airlines" not in page.inner_text("#w-in-carriers")
    shot(page, "24-international-dashboard")


def test_international_filter_bar_and_filters_match_pandas(page, intl):
    goto_intl(page)
    for d in ("fy", "carrier_origin", "carrier", "direction", "country", "origin", "destination", "route"):
        assert page.locator(f"#f-{d}").count() == 1, d
    assert page.locator("#f-airline").count() == 0 and page.locator("#f-segment").count() == 0
    a = intl["air"]
    page.click("#f-direction")
    page.locator(".pop label", has_text="From India").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-direction b').textContent.includes('From India')")
    assert num(kpi(page, "International passengers")) == pytest.approx(a[a.direction == "From India"].pax.sum(), rel=1e-9)
    page.click("#f-carrier")
    page.locator(".pop input[type=search]").fill("emirates")
    page.locator(".pop label:not([hidden])").first.click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-carrier b').textContent.includes('Emirates')")
    x = a[(a.direction == "From India") & (a.carrier == "Emirates")]
    assert num(kpi(page, "International passengers")) == pytest.approx(x.pax.sum(), rel=1e-9)
    shot(page, "25-international-filtered")


def test_fiscal_year_filter_scopes_all_three_international_facts(page, intl):
    goto_intl(page)
    page.click("#f-fy")
    page.locator(".pop label", has_text="2018-19").click()
    page.locator(".pop button", has_text="Done").click()
    page.wait_for_function("!document.querySelector('.card-body.loading') && document.querySelector('#f-fy b').textContent.includes('2018-19')")
    a = intl["air"]
    assert num(kpi(page, "International passengers")) == pytest.approx(a[a.fy == "2018-19"].pax.sum(), rel=1e-9)
    c = intl["country"]
    top = c[c.fy == "2018-19"].groupby("country").pax.sum().sort_values(ascending=False).head(15)
    labels = page.locator("#w-in-countries svg [aria-label='y-axis tick label'] text").evaluate_all("els => els.map(e => e.textContent)")
    assert labels == list(top.index)                                       # the country fact (quarterly) obeys the same fiscal-year filter


def test_quarterly_facts_do_not_offer_monthly_fields(page):
    page.click("#btn-new")
    page.select_option("#b-fact", label="International traffic by country")
    page.wait_for_function("document.querySelector('#s-measures .chip')")
    dims = page.locator("#b-dims .chip").all_inner_texts()
    assert "Country" in dims and "Direction" in dims and "Fiscal quarter" in dims
    assert "Month" not in dims and "Month (timeline)" not in dims          # quarterly source: no fake monthly resolution
    assert page.locator("#s-measures .chip", has_text="International passengers").count() == 1
    page.select_option("#b-fact", label="International traffic by carrier")
    page.wait_for_function("document.querySelector('#s-measures .chip')")
    dims = page.locator("#b-dims .chip").all_inner_texts()
    assert "Month (timeline)" in dims and "Carrier type" in dims


def test_builder_international_routes_use_domain_and_direction(page):
    page.click("#btn-new")
    page.select_option("#b-fact", label="International routes (city pairs)")
    page.wait_for_function("document.querySelector('#s-measures .chip')")
    page.locator("#b-dims .chip", has_text=re.compile(r"^Origin region$")).click()
    page.locator("#b-dims .chip", has_text=re.compile(r"^Direction$")).click()           # exact: "Route (both directions)" also contains the word
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('dim_direction')")
    sql = page.text_content("#b-sql")
    assert "JOIN dim_place AS dim_origin" in sql and "dim_origin.domain" in sql and "JOIN dim_direction" in sql
    page.wait_for_selector("#b-preview svg, #b-preview table")
    rows = page.evaluate("async (s) => (await window.__dgca.query(s)).rows.map(r => [r.origin_domain, r.direction])", sql)
    assert ["Foreign", "To India"] in rows and ["India", "From India"] in rows and ["Foreign", "Outside India"] in rows
    shot(page, "26-builder-international")


def test_ai_prompt_covers_all_facts_and_routes_international_questions(page):
    spec = {"fact": "intl_country_quarterly", "dimensions": ["country"], "measures": ["intl_pax"], "filters": [], "top": None,
            "sort": {"by": "intl_pax", "dir": "desc"}, "limit": 10, "viz": "bar"}
    page.evaluate("s => { window.__seen = null; window.__DGCA_AI_MOCK = async (sys) => { window.__seen = sys; return JSON.stringify(s); }; }", spec)
    page.click("#btn-ai"); page.fill("#ai-q", "which countries have the most passengers"); page.click("#ai-ask")
    page.wait_for_selector("#builder[open]")
    page.wait_for_function("document.querySelector('#b-sql').textContent.includes('dim_country')")
    seen = page.evaluate("window.__seen")
    for fact in ("airline_monthly", "route_monthly", "intl_airline_monthly", "intl_country_quarterly", "intl_citypair_quarterly"):
        assert f'Fact "{fact}"' in seen, fact
    assert "Emirates vs Qatar Airways" in seen and "Which countries have the most passengers" in seen


def test_data_quality_lists_international_checks_and_issues(page):
    page.click("#btn-menu")
    page.click("#menu >> text=Data sources")
    page.wait_for_selector("#dq")
    txt = page.inner_text("#dq")
    assert "91 of 91 table pairs agree" in txt and "0 unexplained" in txt
    assert "flights entirely outside India" in txt or "outside India" in txt
    assert "Q1 2026 city-pair" in txt and "Vancouver-Toronto" in txt and "footnote asterisks" in txt
    assert "Air India Express" in txt and "May 2024" in txt
    shot(page, "27-data-quality-international")


def test_international_dark_and_mobile(page, browser):
    goto_intl(page)
    page.click("#btn-theme")
    page.wait_for_function("!document.querySelector('.card-body.loading')")
    shot(page, "28-international-dark", full_page=True)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector("body[data-ready='1']", timeout=90000)
    pg.select_option("#dash-select", label="★ International traffic")
    pg.wait_for_function("document.querySelectorAll('.card').length === 9 && !document.querySelector('.card-body.loading')")
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    pg.screenshot(path=str(SHOTS / "29-international-mobile.png"), full_page=True)
    ctx.close()


# ================================================================ vendored libraries (no CDN)
VENDOR = ROOT / "web" / "vendor"


def _need_vendor():
    if not (VENDOR / "MANIFEST.json").exists():
        pytest.skip("web/vendor not built (run scripts/vendor.sh)")


def test_vendor_files_match_their_recorded_checksums():
    _need_vendor()
    import hashlib
    m = json.loads((VENDOR / "MANIFEST.json").read_text())
    assert m["duckdb-wasm"] and m["observable-plot"] and {"plot.js", "duckdb/duckdb-eh.wasm", "duckdb/duckdb-browser-eh.worker.js", "duckdb/duckdb.js", "duckdb/extensions/v1.1.1/wasm_eh/parquet.duckdb_extension.wasm"} <= set(m["files"])
    for rel, info in m["files"].items():
        data = (VENDOR / rel).read_bytes()
        assert len(data) == info["bytes"] and hashlib.sha256(data).hexdigest() == info["sha256"], rel


def test_app_runs_with_every_third_party_host_blocked(browser):
    """Offline-capable: the whole app (queries, charts, all dashboards) works when nothing outside this origin is reachable."""
    _need_vendor()
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    external = []
    ctx.route("**/*", lambda route: (external.append(route.request.url), route.abort()) if "localhost" not in route.request.url else route.continue_())
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL)
    pg.wait_for_selector("body[data-ready='1']", timeout=60000)
    assert pg.evaluate("window.__dgca.libs") == {"duckdb": "vendor", "plot": "vendor"}
    assert pg.locator(".card svg").count() >= 3 and not pg.locator(".card .error").count()
    for name in ("★ International traffic", "★ Routes & airports"):
        pg.select_option("#dash-select", label=name)
        pg.wait_for_function("!document.querySelector('.card-body.loading')")
        assert not pg.locator(".card .error").count()
    assert external == [] and errors == [], (external, errors)
    ctx.close()


def test_missing_vendor_files_fall_back_to_the_cdn(browser):
    _need_vendor()
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.route("**/vendor/**", lambda route: route.abort())
    pg = ctx.new_page()
    boot(pg)
    assert pg.evaluate("window.__dgca.libs") == {"duckdb": "cdn", "plot": "cdn"}
    assert pg.locator(".card svg").count() >= 3
    ctx.close()
