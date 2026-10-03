"""Which manifest rows are parseable, and by what. Keeps crawl selection and parsing in one place."""
import re

MONTHLY_RE = re.compile(r"^MONTHLY TRAFFIC AND OPERATING STATISTICS OF .*\bON SCHEDULED (DOMESTIC|INTERNATIONAL) SERVICES", re.I | re.S)


INTL_Q_RE = re.compile(r"/airTransport/international/quaterly/(\d\d)Q([1-4])_([1-4])\.xlsx$", re.I)
CITYPAIR_RE = re.compile(r"/airTransport/domestic/monthly/[^/]*CITY\s*PAIR[^/]*\.xlsx$", re.I)
CAL_RE = re.compile(r"/airTransport/domestic/monthly/(?![^/]*CITY\s*PAIR)[^/]+\.xlsx$", re.I)


def operator_from_title(title: str) -> str:
    return re.sub(r"\s*\((EXCEL|PDF)\)\s*$", "", title or "", flags=re.I).strip()


def dataset_for(title: str, ext: str, path: str = "") -> dict | None:
    """Return {dataset, segment?} if this file is something we know how to parse, else None."""
    if ext == "xlsx" and path and (m := INTL_Q_RE.search(path or "")):
        return {"dataset": "intl_quarterly", "segment": "international", "table": int(m[3]), "year": 2000 + int(m[1]), "quarter": int(m[2])}
    if ext == "xlsx" and path and CITYPAIR_RE.search(path or ""):
        return {"dataset": "city_pair_monthly", "segment": "domestic"}
    if ext == "xlsx" and path and CAL_RE.search(path or ""):
        return {"dataset": "airline_monthly_cal", "segment": None}
    t = " ".join((title or "").split())
    if ext in ("xlsx",) and (m := MONTHLY_RE.search(t)):
        return {"dataset": "airline_monthly", "segment": m.group(1).lower()}
    return None
