"""Discover files in the air-transport statistics section by walking index -> year page -> entity page."""
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .. import config
from ..http import get_html, norm_url

FILE_EXT = ("pdf", "xlsx", "xls", "zip")
YEAR_RE = re.compile(r"(\d{4})-(\d{2,4})")


def _links(html: str) -> list[tuple[str, str]]:
    """(attr-url, anchor text) for every data-url / url link, whitespace-normalised."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all(True, attrs={"data-url": True}):
        out.append((a["data-url"].strip(), " ".join(a.get_text().split())))
    for tag in soup.find_all(True, attrs={"url": True}):
        out.append((tag["url"].strip(), " ".join(tag.get_text().split())))
    return out


def fy_label(text: str) -> str | None:
    m = YEAR_RE.search(text)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    return f"{a}-{b[-2:]}"


def year_pages(index_html: str) -> list[dict]:
    """Year landing pages listed on the yearly index. `fy` is read from the row text where available."""
    soup = BeautifulSoup(index_html, "lxml")
    pages = {}
    for tr in soup.find_all("tr"):
        text = " ".join(tr.get_text().split())
        fy = fy_label(text)
        for url, _ in _links(str(tr)):
            if url.endswith(".html") and config.STATS_BASE in url and "FLEET" not in url:
                pages.setdefault(url, fy)
    return [{"portal_path": u, "fy": fy} for u, fy in pages.items()]


def entity_pages(year_html: str) -> list[dict]:
    """Entity pages (airlines etc.) linked from a year page: `<n>/Normal/<ENTITY>.html`."""
    seen, out = set(), []
    for url, text in _links(year_html):
        if "/Normal/" in url and url.endswith(".html") and url not in seen:
            seen.add(url)
            entity = unquote(url.rsplit("/", 1)[1][:-5]).strip()
            out.append({"portal_path": url, "entity": entity})
    return out


def file_rows(entity_html: str, page_path: str, entity: str, fy: str | None) -> list[dict]:
    """One manifest record per pdf/xlsx link, tagged with table id and title from its table row."""
    soup = BeautifulSoup(entity_html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(td.get_text().split()) for td in tr.find_all("td")]
        links = [u for u, _ in _links(str(tr)) if u.lower().rsplit(".", 1)[-1] in FILE_EXT]
        if not links or len(cells) < 2:
            continue
        m = re.search(r"(\d+\.\d+)", cells[0])
        table_id = m.group(1) if m else cells[0]
        title = cells[1]
        row_fy = fy or fy_label(title)
        for u in links:
            rows.append({
                "s3_url": config.S3 + norm_url(u.split("jsp/dgca/", 1)[-1]),
                "portal_path": u, "section": config.SECTION, "fy": row_fy, "entity": entity,
                "table_id": table_id, "title": title, "ext": u.lower().rsplit(".", 1)[-1],
                "source_page": page_path,
            })
    return rows


def discover(sess, refresh=False, years: set[str] | None = None, log=print) -> tuple[list[dict], list[dict]]:
    """Walk the section. Returns (file records, unsupported year pages)."""
    index = get_html(sess, config.STATS_INDEX, refresh=True)
    if index is None:
        raise RuntimeError("statistics index not found")
    records, unsupported = [], []
    for yp in year_pages(index):
        html = get_html(sess, config.PORTAL + yp["portal_path"], refresh)
        if html is None:
            unsupported.append({**yp, "reason": "year page 404"})
            continue
        fy = yp["fy"] or fy_label(re.sub(r"<[^>]+>", " ", html)[:3000])
        if years and fy not in years:
            continue
        ents = entity_pages(html)
        if not ents:
            unsupported.append({**yp, "fy": fy, "reason": "no entity pages (older layout)"})
            continue
        log(f"{fy}: {len(ents)} entity pages")
        for e in ents:
            eh = get_html(sess, config.PORTAL + e["portal_path"], refresh)
            if eh is None:
                unsupported.append({**e, "fy": fy, "reason": "entity page 404"})
                continue
            records += file_rows(eh, e["portal_path"], e["entity"], fy)
    return records, unsupported
