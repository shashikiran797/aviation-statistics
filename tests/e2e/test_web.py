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
    d = pd.concat(pd.read_parquet(f) for f in glob.glob(str(ROOT / "data/interim/airline_monthly/*.parquet")))
    d["e"] = d.entity.str.replace(r"\s+excel$", "", regex=True)
    return d[~d.e.isin(AGG)]


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


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and pg.errors.append(m.text))
    pg.goto(URL)
    pg.wait_for_selector("body[data-ready='1']", timeout=90000)
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


def test_boot_renders_all_starter_widgets_and_kpis_match_pandas(page, interim):
    assert page.locator(".card").count() == 5
    assert page.locator(".card svg").count() >= 3
    assert not page.locator(".card .error").count()
    assert num(kpi(page, "Passengers carried")) == pytest.approx(interim.pax.sum(), rel=0.001)
    assert num(kpi(page, "Departures")) == pytest.approx(interim.departures.sum(), rel=0.001)
    plf = 100 * interim.pax_km_million.sum() / interim.ask_million.sum()
    assert num(kpi(page, "Passenger load factor")) == pytest.approx(plf, abs=0.06)
    shot(page, "01-overview")


def test_wasm_matches_native_duckdb_on_every_vector(page):
    import duckdb
    vec = json.loads((ROOT / "tests/vectors/spec_cases.json").read_text())["cases"]
    d = sorted((ROOT / "web/data").glob("*/"))[0] if False else next(p for p in (ROOT / "web/data").iterdir() if p.is_dir())
    con = duckdb.connect()
    for t in ("fact_airline_monthly", "dim_airline", "dim_period", "dim_segment"):
        con.execute(f"create view {t} as select * from '{d}/{t}.parquet'")
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
    assert n >= 8


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
    xd = x[x.segment == "domestic"]
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
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5)
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


def test_widget_table_toggle_and_csv_download(page):
    page.locator("#w-pax-fy button[title='Toggle table view']").click()
    assert page.locator("#w-pax-fy table.t tbody tr").count() == 10
    with page.expect_download() as dl:
        page.locator("#w-pax-fy button[title='Download CSV']").click()
    csv = Path(dl.value.path()).read_text().splitlines()
    assert csv[0] == "fy,segment,pax" and len(csv) == 11
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
    page.click("#btn-menu")
    with page.expect_file_chooser() as fc:
        page.click("#menu >> text=Import JSON")
    fc.value.set_files(dl.value.path())
    page.wait_for_function("document.querySelectorAll('#dash-select option').length === 4")


def test_sql_console_allows_select_and_blocks_everything_else(page):
    page.click("#btn-menu")
    page.click("#menu >> text=Advanced")
    page.click("text=Run (read-only)")
    page.wait_for_selector("#modal table.t")
    assert page.locator("#modal table.t tbody tr").count() == 5
    for bad in ["DROP TABLE dim_airline", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('/etc/passwd')", "COPY dim_airline TO 'x.csv'"]:
        page.fill("#modal textarea", bad)
        page.click("text=Run (read-only)")
        expect(page.locator("#modal .error")).to_contain_text("Only a single SELECT")
    shot(page, "07-sql-console")


def test_sources_modal(page):
    page.click("#btn-menu")
    page.click("#menu >> text=Data sources")
    page.wait_for_selector("#modal table.t")
    assert "Excel files parsed" in page.inner_text("#modal")
    assert page.locator("#modal table.t tbody tr").count() == 5
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
    assert page.evaluate("async () => (await window.__dgca.query('select count(*) n from dim_airline')).rows[0].n") == 24


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
    assert page.locator("#view-dash").is_hidden() and page.locator("#btn-new").is_hidden()
    shot(page, "14-catalog")
    page.fill("#cat-q", "aerodrome")
    page.wait_for_function(f"document.querySelector('#cat-summary').textContent.includes('aerodrome')")
    narrowed = _summary_count(page)
    assert 0 < narrowed < total
    rows = page.locator("#cat-results .cat-row")
    assert "aerodrome" in rows.first.inner_text().lower()
    href = rows.first.locator("a").first.get_attribute("href")
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
