"""Parse DGCA 'city pair wise domestic traffic' monthly xlsx files into directional route rows.

Layouts seen 2015-2026: 5 columns (passengers only) or 9+ columns (passengers, freight, mail; extra blank columns in 2025+).
Columns are located by header text, not position. Each source row is an unordered pair (CITY 1, CITY 2) with a figure *to*
and *from* city 2; it is emitted as two directional rows. Every file is validated against its own SUB TOTAL / TOTAL footer.
"""
import re

import pandas as pd

from .monthly import LayoutError

MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
MEASURES = {"PASSENGERS": "pax", "FREIGHT": "freight_t", "MAIL": "mail_t"}
TITLE_RE = re.compile(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\W{0,4}(20\d\d)", re.I)


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    s = str(v).strip().replace(",", "")
    if s in ("-", "", "–", "nan", "NaN"):
        return 0.0
    return float(s)


def period_from_text(text: str) -> pd.Timestamp | None:
    m = TITLE_RE.search(text or "")
    return pd.Timestamp(int(m[2]), MONTHS.index(m[1].upper()[:3]) + 1, 1) if m else None


def read(path, filename: str = "") -> dict:
    raw = pd.read_excel(path, header=None).dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    # header row = the first row (within the top 8) that has a "CITY 1"-style cell; S.No. is optional and spelled many ways
    head = next((i for i in range(min(8, len(raw))) if any(re.match(r"CITY\s*1\b", " ".join(str(v).upper().split())) for v in raw.iloc[i])), None)
    if head is None:
        raise LayoutError("no 'CITY 1' header row - not a city-pair table")
    title = " ".join(str(v) for v in raw.iloc[:head].stack().dropna().astype(str))
    cols = {}
    for j in range(raw.shape[1]):
        h = " ".join(str(raw.iloc[head, j]).upper().split())
        if re.match(r"CITY\s*1\b", h):
            cols["city1"] = j
        elif re.match(r"CITY\s*2\b", h):
            cols["city2"] = j
        else:
            for word, name in MEASURES.items():
                if re.match(word[:5], h) and re.search(r"\bTO CITY\s*2", h):          # tolerates the 'PASSENEGER' typo
                    cols[name + "_to"] = j
                elif re.match(word[:5], h) and re.search(r"\bFROM CITY\s*2", h):
                    cols[name + "_from"] = j
    if not {"city1", "city2", "pax_to", "pax_from"} <= set(cols):
        raise LayoutError(f"missing columns, found {sorted(cols)}")
    body, sub, total = [], None, None
    c1, c2 = cols["city1"], cols["city2"]
    for i in range(head + 1, len(raw)):
        lead = " ".join(str(v).upper() for v in raw.iloc[i, : c1 + 1] if pd.notna(v)).strip()
        if re.search(r"\bSUB\s*TOTALS?\b", lead):
            sub = raw.iloc[i]
        elif re.fullmatch(r"(S\.?\s*NO\.?\s*)?(GRAND\s+)?TOTALS?", lead) or lead.startswith("TOTAL"):
            if not lead.startswith("GRAND"):
                total = raw.iloc[i]
        elif pd.notna(raw.iloc[i, c1]) and pd.notna(raw.iloc[i, c2]) and not lead.upper().startswith("NOTE"):
            body.append(raw.iloc[i])
    if not body:
        raise LayoutError("no data rows")
    df = pd.DataFrame(body).reset_index(drop=True)
    for name, j in cols.items():
        df[name] = df[j].map(lambda v, n=name: " ".join(str(v).split()) if n.startswith("city") else _num(v))
    df = df[list(cols)]
    # the same pair can legitimately appear twice (e.g. two airports of one city); the footer counts both, so we add them
    key = ["city1", "city2"]
    dupes = int(df.duplicated(key).sum())
    if dupes:
        df = df.groupby(key, as_index=False, sort=False).sum(numeric_only=True)
    t_period = period_from_text(title)
    f_period = period_from_text(filename.replace("_", " "))
    tp = None
    if total is not None:
        # modern layout: TOTAL carries to+from in one cell; 2015-18 layout: TOTAL holds per-direction sums (GRAND TOTAL is both)
        to_, fr_ = total.iloc[cols["pax_to"]], total.iloc[cols["pax_from"]]
        if pd.notna(to_) and pd.notna(fr_):
            if sub is None:
                sub = total
            tp = _num(to_) + _num(fr_)
        else:
            nums = [_num(v) for v in (to_, fr_) if pd.notna(v)]
            tp = max(nums) if nums else None
    return {"total_pax": tp, "rows": df, "title": title, "title_period": t_period, "file_period": f_period, "merged_duplicates": dupes,
            "provisional": "PROVISIONAL" in title.upper(), "revised": "REVISED" in filename.upper(),
            "sub": None if sub is None else {k: _num(sub[j]) for k, j in cols.items() if not k.startswith("city")}}


def validate(parsed: dict) -> list[str]:
    """Hard errors: footer totals must equal the sum of the rows; title and filename must name the same month."""
    p, df = [], parsed["rows"]
    tp, fp = parsed["title_period"], parsed["file_period"]
    if tp is not None and fp is not None and tp != fp:
        p.append(f"title says {tp:%b %Y} but file name says {fp:%b %Y}")
    if tp is None and fp is None:
        p.append("cannot determine month")
    sub = parsed["sub"]
    tp = parsed.get("total_pax")
    if tp is not None:
        both = df.pax_to.sum() + df.pax_from.sum()
        if abs(both - tp) > max(1.0, tp * 2e-3):
            p.append(f"pax to+from {both:.6g} != TOTAL {tp:.6g}")
    if sub is None and tp is None:
        p.append("no SUB TOTAL/TOTAL row to reconcile against")
    elif sub is not None:
        for k, v in sub.items():
            if k in df and abs(df[k].sum() - v) > max(1.0, abs(v) * 2e-3):
                p.append(f"{k}: rows sum {df[k].sum():.6g} != SUB TOTAL {v:.6g}")
    if parsed.get("merged_duplicates"):
        p.append(f"warn: {parsed['merged_duplicates']} duplicated city pairs were added together")
    if (df.select_dtypes("number") < 0).any().any():
        p.append("negative values")
    return p


def directional(parsed: dict, period: pd.Timestamp) -> pd.DataFrame:
    """Two rows per source pair: city1 -> city2 (the 'to city 2' figures) and city2 -> city1 ('from city 2')."""
    d = parsed["rows"]
    names = [m for m in ("pax", "freight_t", "mail_t") if f"{m}_to" in d]
    out = []
    for o, de, suffix in (("city1", "city2", "_to"), ("city2", "city1", "_from")):
        x = pd.DataFrame({"origin": d[o], "destination": d[de], **{m: d[m + suffix] for m in names}})
        out.append(x)
    r = pd.concat(out, ignore_index=True)
    for m in ("freight_t", "mail_t"):
        if m not in r:
            r[m] = float("nan")          # not published before 2018: unknown, not zero
    r.insert(0, "period", period)
    r["provisional"] = parsed["provisional"]
    r["revised"] = parsed["revised"]
    return r
