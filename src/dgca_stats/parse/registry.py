"""Which manifest rows are parseable, and by what. Keeps crawl selection and parsing in one place."""
import re

MONTHLY_RE = re.compile(r"^MONTHLY TRAFFIC AND OPERATING STATISTICS OF .*\bON SCHEDULED (DOMESTIC|INTERNATIONAL) SERVICES", re.I | re.S)


def dataset_for(title: str, ext: str) -> dict | None:
    """Return {dataset, segment} if this file is something we know how to parse, else None."""
    t = " ".join((title or "").split())
    if ext in ("xlsx",) and (m := MONTHLY_RE.search(t)):
        return {"dataset": "airline_monthly", "segment": m.group(1).lower()}
    return None
