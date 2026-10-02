import pytest

from dgca_stats import config
from dgca_stats.crawl import site

HTML = """
<h5>List of Type Certificates</h5>
<table class="t"><thead><tr><th>S.No</th><th>Aircraft</th><th>Certificate</th></tr></thead><tbody>
<tr><td>1</td><td>HAL Dornier 228</td><td><a data-url="jsp/dgca/InventoryList/aircraft/certification/TC/Dornier 228.pdf">View</a></td></tr>
<tr><td>2</td><td>Saras</td><td><a data-url="4185/4189/servicename">More</a></td></tr></tbody></table>
<a href="https://old.dgca.nic.in/pub/report.pdf">old report</a><a href="#top">skip</a><a href="mailto:x@y.z">mail</a>
<span url="IncidentReports/500006/0/viewApplicationDtlsReq">Incidents</span>
"""


def test_route_patterns():
    assert site.SERVICE_RE.match("4184/4188/servicename")
    assert not site.SERVICE_RE.match("4184/servicename")
    m = site.DYN_RE.match("AccidentReports/500005/0/viewApplicationDtlsReq")
    assert m and m[1] == "AccidentReports" and m[2] == "500005" and m[4] == "viewApplicationDtlsReq"
    assert site.DYN_RE.match("NodalOfficers/0/500008/viewApplicationDtlsReqPDF")


@pytest.mark.parametrize("p,e", [("a/b/Report.PDF", "pdf"), ("x.xlsx?v=1", "xlsx"), ("page.html", None), ("noext", None), ("a.b/c", None)])
def test_ext_of(p, e):
    assert site.ext_of(p) == e


def test_links_in_collects_portal_links_and_file_hrefs_only():
    got = {u for u, _ in site.links_in(HTML)}
    assert "jsp/dgca/InventoryList/aircraft/certification/TC/Dornier 228.pdf" in got
    assert "4185/4189/servicename" in got and "IncidentReports/500006/0/viewApplicationDtlsReq" in got
    assert "https://old.dgca.nic.in/pub/report.pdf" in got
    assert not any(u.startswith(("#", "mailto")) for u in got)


def test_s3_url_mapping_encodes_spaces():
    u = site.s3_url("jsp/dgca/InventoryList/aircraft/certification/TC/Dornier 228.pdf")
    assert u == config.S3 + "InventoryList/aircraft/certification/TC/Dornier%20228.pdf"
    assert site.s3_url("4185/4189/servicename") is None


def test_extract_tables_keeps_text_and_skips_tiny():
    tabs = site.extract_tables(HTML + "<table><tr><td>only one row</td></tr></table>")
    assert len(tabs) == 1 and list(tabs[0].columns) == ["S.No", "Aircraft", "Certificate"]
    assert tabs[0].iloc[0]["Aircraft"] == "HAL Dornier 228"
    assert str(tabs[0].dtypes.iloc[0]) == "string"


def test_manifest_rows_for_static_and_dynamic_files():
    rows = site.to_manifest_rows([
        {"route": "jsp/dgca/InventoryList/a/b c.pdf", "crumbs": ["Aircraft", "Certification", "Design 2019-20"], "ext": "pdf", "dynamic": False},
        {"route": "NodalOfficers/0/500008/viewApplicationDtlsReqPDF", "crumbs": ["About", "Nodal Officers"], "ext": "pdf", "dynamic": True},
        {"route": "http://example.org/x.pdf", "crumbs": ["Misc"], "ext": "pdf", "dynamic": False},
        {"route": "relative/unmapped.pdf", "crumbs": ["Misc"], "ext": "pdf", "dynamic": False},   # no S3 mapping -> dropped, never guessed
    ])
    assert len(rows) == 3
    st, dyn, ext = rows
    assert st["s3_url"].endswith("/b%20c.pdf") and st["section"] == "site/aircraft" and st["fy"] == "2019-20"
    assert dyn["s3_url"] == "dgca-dynamic://NodalOfficers/0/500008/viewApplicationDtlsReqPDF"
    assert ext["s3_url"] == "http://example.org/x.pdf"
