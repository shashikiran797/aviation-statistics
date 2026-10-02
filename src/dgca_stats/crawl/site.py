"""Whole-site crawl: walk every menu entry in the portal's sitemap and everything reachable from it.

The portal serves three kinds of nodes:
  * static pages          jsp/dgca/....html|jsp           -> GET
  * rule-book content     <ruleBookId>/<contentId>/servicename -> POST scan? (JSON with an HTML fragment)
  * dynamic applications  <Name>/<rb>/<ct>/viewApplicationDtlsReq[PDF] -> POST scan? (HTML page or encrypted attachment id)
Files (pdf/xlsx/zip/...) live on public S3. A real browser session is used because the portal sets WAF cookies via JS.
Everything fetched is cached on disk, so re-runs are cheap and the crawl is resumable.
"""
import hashlib
import json
import re
from collections import deque
from io import StringIO
from urllib.parse import unquote, urljoin

import pandas as pd
from bs4 import BeautifulSoup

from .. import config, manifest
from ..http import _throttle, norm_url

FILE_EXT = ("pdf", "xlsx", "xls", "csv", "zip", "doc", "docx", "ppt", "pptx", "xlsm", "odt", "rar", "txt", "jpg", "png")
SERVICE_RE = re.compile(r"^(\d+)/(\d+)/servicename$")
DYN_RE = re.compile(r"^(\w+)/(\d+)/(\d+)/(viewApplicationDtlsReq\w*)$")
HTML_RE = re.compile(r"^(\w+)/(\d+)/(\d+)/html$")          # e.g. quat/4268/9661/html (quarterly stats)
DYNRULE_RE = re.compile(r"^(\w+)/(\d+)/(\d+)/(viewDynamicRule\w*)$")   # circulars / acts lists
ATTACH_RE = re.compile(r"^dynamicPdf/(.+)$")
FORM = dict(screenId="10000001", classification="", attachId="", langType="1", baseLocale="")
CACHE = config.DATA / "raw" / "_site"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60] or "root"


def ext_of(path: str) -> str | None:
    last = path.split("?")[0].rsplit("/", 1)[-1]
    e = last.rsplit(".", 1)[-1].lower() if "." in last else ""
    return e if e in FILE_EXT else None


def decode(b: bytes) -> str:
    """Legacy pages are Windows-1252, newer ones UTF-8."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


class Portal:
    def __init__(self, log=print):
        self.log, self.n_requests = log, 0

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        self.ctx = self.browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36 dgca-stats/0.1")
        pg = self.ctx.new_page()
        pg.goto(config.PORTAL + "?baseLocale=en_US", wait_until="networkidle", timeout=90000)
        with pg.expect_navigation():
            pg.evaluate("langChange('en_US')")
        pg.wait_for_load_state("networkidle")
        pg.close()
        return self

    def __exit__(self, *a):
        self.browser.close()
        self._pw.stop()

    def _cached(self, key: str, fn, refresh: bool):
        CACHE.mkdir(parents=True, exist_ok=True)
        f = CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
        if f.exists() and not refresh:
            return json.loads(f.read_text())
        _throttle()
        self.n_requests += 1
        status, text = fn()
        rec = {"status": status, "text": text}
        if status == 200:
            f.write_text(json.dumps(rec))
        return rec

    def get(self, path: str, refresh=False):
        url = config.PORTAL + norm_url(path)
        return self._cached("GET " + url, lambda: (lambda r: (r.status, decode(r.body())))(self.ctx.request.get(url, timeout=60000)), refresh)

    def scan(self, refresh=False, **fields):
        data = {**FORM, **fields}
        key = "POST " + json.dumps(data, sort_keys=True)
        hdr = {"X-Requested-With": "XMLHttpRequest", "Referer": config.PORTAL}
        return self._cached(key, lambda: (lambda r: (r.status, decode(r.body())))(self.ctx.request.post(config.PORTAL + "scan?", form=data, headers=hdr, timeout=60000)), refresh)


GENERIC = {"", "click", "click here", "here", "view", "download", "pdf", "excel", "xls", "xlsx", "open"}


def _label(tag) -> str:
    """Link text, or - for generic 'Click here' links - the other cells of its table row (e.g. operator name)."""
    txt = " ".join(tag.get_text().split())
    if txt.lower() in GENERIC:
        tr = tag.find_parent("tr")
        if tr:
            parts = []
            for td in tr.find_all("td"):
                if td.find(attrs={"data-url": True}) or td.find("a", href=True):
                    continue
                t = " ".join(td.get_text().split())
                if t and not re.fullmatch(r"[\d.]+", t):
                    parts.append(t)
            col = ""
            td = tag.find_parent("td")
            if td and tr.find_parent("table"):
                heads = [" ".join(h.get_text().split()) for h in tr.find_parent("table").find_all("th")]
                cells = tr.find_all("td")
                if td in cells and len(heads) == len(cells):
                    col = heads[cells.index(td)]
            if parts:
                return " ".join(parts) + (f" ({col})" if col and col.lower() not in GENERIC - {"pdf", "excel", "xls", "xlsx"} else "")
    return txt


def links_in(html: str, base: str = "") -> list[tuple[str, str]]:
    """(target, text) for portal links (data-url/url attrs) and plain hrefs to files or external sites."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for t in soup.find_all(True, attrs={"data-url": True}):
        out.append((t["data-url"].strip(), _label(t)))
    for t in soup.find_all(True, attrs={"url": True}):
        out.append((t["url"].strip(), _label(t)))
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith(("#", "javascript", "mailto", "tel")) or not h:
            continue
        if ext_of(h) or h.startswith("http"):
            out.append((h, _label(a)))
    return [(u, t) for u, t in out if u]


def s3_url(path: str) -> str | None:
    if "jsp/dgca/" in path:
        return config.S3 + norm_url(path.split("jsp/dgca/", 1)[1])
    if path.startswith("http") and "amazonaws.com" in path:
        return path
    return None


def extract_tables(html: str):
    """HTML tables in a page fragment -> list of DataFrames (all-string), skipping navigation-only tables."""
    if "<table" not in html.lower():
        return []
    try:
        dfs = pd.read_html(StringIO(html), flavor="lxml")
    except ValueError:
        return []
    out = []
    for d in dfs:
        if d.shape[0] >= 2 and d.shape[1] >= 2:
            d.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in d.columns]
            out.append(d.astype("string"))
    return out


def seeds(portal: Portal) -> list[dict]:
    """Every menu entry in the sitemap, with its English breadcrumb."""
    html = portal.get("jsp/dgca/footerLink/sitemap.jsp")["text"]
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all(attrs={"data-url": True}):
        chain = []
        for p in a.parents:
            if p.name == "li":
                first = p.find("a")
                chain.append(" ".join((first.get_text() if first else "").split()))
        crumbs = [c for c in reversed(chain) if c]
        title = " ".join(a.get_text().split())
        if not crumbs or crumbs[-1] != title:
            crumbs.append(title)
        u = a["data-url"].strip()
        if u not in seen:
            seen.add(u)
            out.append({"route": u, "crumbs": crumbs})
    return out


def crawl(portal: Portal, refresh=False, max_nodes=4000, max_depth=5, log=print):
    pages, files, tables = [], [], []
    queue = deque((s["route"], s["crumbs"], 0, "sitemap") for s in seeds(portal))
    seen = set()
    while queue and len(pages) < max_nodes:
        route, crumbs, depth, parent = queue.popleft()
        key = unquote(route).strip()
        if key in seen:
            continue
        seen.add(key)
        title = crumbs[-1] if crumbs else key
        rec = dict(route=key, parent=parent, depth=depth, title=title, section_path=" > ".join(crumbs), kind="unhandled",
                   status="skipped", http_status=None, n_links=0, n_files=0, n_tables=0, error=None)
        html = None
        try:
            e = ext_of(key)
            if e and not key.startswith("http"):                       # a file
                files.append(dict(route=key, crumbs=crumbs, ext=e, dynamic=False))
                rec.update(kind="file", status="ok")
            elif key.startswith("http"):
                rec.update(kind="external", status="ok")
            elif m := SERVICE_RE.match(key):
                r = portal.scan(refresh, requestType="ApplicationRH", actionVal="viewStaticData", ruleBookId=m[1], contentId=m[2], serviceName="getParentData", attr="")
                rec.update(kind="rulebook", http_status=r["status"])
                if r["status"] == 200:
                    j = json.loads(r["text"])
                    items = j.get("ruleBookContentDtlsList") or ([j] if j.get("contentText") else [])
                    html = "\n".join(i.get("contentText") or "" for i in items)
                    rec["status"] = "ok"
                    if items and items[0].get("contentName"):
                        rec["title"] = items[0]["contentName"] if rec["title"] == key else rec["title"]
            elif m := DYN_RE.match(key):
                r = portal.scan(refresh, requestType="ApplicationRH", actionVal=m[4], ruleBookId=m[2], contentId=m[3], serviceName="", attr=m[1])
                rec.update(kind="dynamic", http_status=r["status"])
                if r["status"] == 200:
                    rec["status"] = "ok"
                    if m[4].endswith("PDF"):                           # returns an encrypted attachment token, not HTML
                        rec["kind"] = "dynamic_pdf"
                        files.append(dict(route=key, crumbs=crumbs, ext="pdf", dynamic=True))
                    else:
                        html = r["text"]
            elif m := HTML_RE.match(key):
                svc = "getParentData" if m[1] == "yearly" else "fetchRulebookContentDtlsList"
                r = portal.scan(refresh, requestType="ApplicationRH", actionVal="viewStaticData", ruleBookId=m[2], contentId=m[3], serviceName=svc, attr="")
                rec.update(kind="rulebook", http_status=r["status"])
                if r["status"] == 200:
                    j = json.loads(r["text"])
                    items = j.get("ruleBookContentDtlsList") or ([j] if j.get("contentText") else [])
                    html, rec["status"] = "\n".join(i.get("contentText") or "" for i in items), "ok"
                    if items and items[0].get("contentName"):
                        rec["title"] = items[0]["contentName"]
                        rec["content_name"] = items[0]["contentName"]
            elif m := DYNRULE_RE.match(key):
                svc = "fetchRulebookContentDtlsList" if "ContLvl2" in m[4] else ""
                r = portal.scan(refresh, requestType="ApplicationRH", actionVal=m[4], ruleBookId=m[2], contentId=m[3], serviceName=svc, attr=m[1])
                rec.update(kind="dynamic", http_status=r["status"])
                if r["status"] == 200:
                    html, rec["status"] = r["text"], "ok"
            elif m := ATTACH_RE.match(key):
                files.append(dict(route=key, crumbs=crumbs, ext="pdf", dynamic=True))
                rec.update(kind="dynamic_pdf", status="ok")
            elif key.startswith(("jsp/", "scan?")):
                r = portal.get(key, refresh)
                rec.update(kind="page", http_status=r["status"])
                if r["status"] == 200:
                    html, rec["status"] = r["text"], "ok"
            if html:
                ls = links_in(html)
                rec["n_links"] = len(ls)
                for i, df in enumerate(extract_tables(html)):
                    tables.append(dict(route=key, idx=i, title=rec["title"], section_path=rec["section_path"], df=df))
                rec["n_tables"] = sum(1 for t in tables if t["route"] == key)
                for u, t in ls:
                    if ext_of(u) or u.startswith("http"):
                        if not u.startswith("http") or s3_url(u) or ext_of(u):
                            rec["n_files"] += 1
                    if depth < max_depth:
                        queue.append((u, crumbs + [t or u], depth + 1, key))
        except Exception as ex:  # one bad page must not stop the crawl
            rec.update(status="error", error=str(ex)[:300])
        pages.append(rec)
        if len(pages) % 50 == 0:
            log(f"  {len(pages)} nodes, {len(queue)} queued, {len(files)} files, {len(tables)} tables")
    log(f"site crawl: {len(pages)} nodes, {len(files)} file links, {len(tables)} html tables, {portal.n_requests} live requests")
    return pages, files, tables


def to_manifest_rows(files: list[dict]) -> list[dict]:
    rows = []
    for f in files:
        route = f["route"]
        crumbs = f["crumbs"]
        sec = slug(crumbs[0]) if crumbs else "site"
        title = crumbs[-1] if crumbs else route
        u = s3_url(route) if not f["dynamic"] else None
        if not u and f["dynamic"]:
            u = "dgca-dynamic://" + route                           # stable identity (tokens are deterministic AES values)
        elif not u:
            if route.startswith("http"):
                u = route
            else:
                continue
        fy = None
        m = re.search(r"(\d{4})-(\d{2,4})", title + " " + route)
        if m:
            fy = f"{m[1]}-{m[2][-2:]}"
        rows.append(dict(s3_url=u, portal_path=route, section="site/" + sec, fy=fy, entity=" > ".join(crumbs[:-1])[:300] or None,
                         table_id=None, title=title[:300], ext=f["ext"], source_page=route))
    return rows
