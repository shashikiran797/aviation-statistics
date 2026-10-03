"""Parse DGCA 'quarterly international traffic' xlsx tables (scheduled services, 2015 Q1 onward).

Four tables per quarter (file suffix _1.._4):
  1 airline-wise (quarter totals)   2 airline-wise by month   3 country-wise   4 city-pair-wise (two blocks)
All are published as "to India" / "from India" columns; we keep that as a `direction` and emit long rows.
Every file is validated against the totals printed in the sheet itself (section totals, SUB TOTAL, GRAND TOTAL).
"""
import re

import pandas as pd

from .monthly import LayoutError

MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
QTR_MONTHS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}
SNO_RE = re.compile(r"^S\.?\s*L?\.?\s*NO\.?$", re.I)
SECTION_RE = re.compile(r"^(DOMESTIC|FOREIGN)\s+(CARRIERS|OPERATORS|AIRLINES)\s*$", re.I)


def _num(v) -> float:
    """Cell -> number. '-' is zero; trailing footnote asterisks ('288.0*') are stripped (counted via footnotes())."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    s = str(v).strip().replace(",", "").rstrip("*").strip()
    return 0.0 if s in ("-", "", "–", "nan", "NaN") else float(s)


def footnotes(raw) -> int:
    """Number of data cells carrying a footnote marker."""
    return int(sum(isinstance(v, str) and v.strip().endswith("*") and re.fullmatch(r"[\d.,]+\*+", v.strip()) is not None for v in raw.to_numpy().ravel()))


def _tol(measure: str) -> float:
    """Relative tolerance against the totals printed in the sheet: freight is published with rounding/estimates."""
    return 5e-3 if measure == "freight_t" else 2e-3


FREIGHT_SOFT = 0.015      # freight totals differing by up to 1.5% are recorded as warnings (footnoted/estimated cells), never silently ignored


def _severity(measure: str, got: float, want: float, label: str, soft_ok: bool = False) -> str | None:
    """None when it reconciles; 'warn: ...' for tolerable freight differences or misprinted sub-totals; plain text = hard error."""
    diff = abs(got - want)
    if diff <= max(1.0, abs(want) * _tol(measure)):
        return None
    rel = diff / max(abs(want), 1.0)
    msg = f"{label}: rows sum {got:.6g} != printed {want:.6g} ({rel:.2%})"
    if soft_ok or (measure == "freight_t" and rel <= FREIGHT_SOFT):
        return "warn: " + msg
    return msg


def _blank(row) -> bool:
    return all(pd.isna(v) or str(v).strip() in ("", "nan") for v in row)


def _frame(path) -> pd.DataFrame:
    return pd.read_excel(path, header=None).dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)


def quarter_from(name: str, title: str) -> tuple[int, int]:
    """(year, quarter) from the file name (authoritative) - the title is only used to cross-check."""
    m = re.match(r"(\d\d)Q([1-4])", name)
    if not m:
        raise LayoutError("file name has no quarter code")
    return 2000 + int(m[1]), int(m[2])


def title_quarter(title: str) -> tuple[int, int] | None:
    t = title.upper()
    y = re.search(r"\b(20\d\d)\b", t)
    for q, months in QTR_MONTHS.items():
        if re.search(rf"\b{MONTHS[months[0] - 1][:3]}[A-Z]*\W*(TO|-|–)?\W*{MONTHS[months[-1] - 1][:3]}", t) and y:
            return int(y[1]), q
    return None


def _header_row(raw) -> int:
    for i in range(min(8, len(raw))):
        if SNO_RE.match(str(raw.iloc[i, 0]).strip().replace(" ", "")):
            return i
    raise LayoutError("no S.No. header row")


def _cols(raw, head, nblocks=1):
    """Locate 'passengers/freight to/from india|city 2' columns by header text (header may span 2 rows)."""
    out = []
    for j in range(raw.shape[1]):
        h = " ".join(str(raw.iloc[r, j]).upper() for r in range(head, min(head + 2, len(raw))) if pd.notna(raw.iloc[r, j]))
        h = " ".join(h.replace("\\N", " ").split())
        m = re.search(r"(PASSENGERS?|PAX|FREIGHT|FRT)\s+(TO|FROM)\s+(INDIA|CITY\s*2)", h)
        if m:
            out.append((j, "pax" if m[1][0] == "P" else "freight_t", "To India" if m[2] == "TO" else "From India"))
    return out


def _title(raw, head) -> str:
    return " ".join(str(v) for v in raw.iloc[:head].stack().dropna().astype(str))


def read_named(path, kind: str) -> dict:
    """kind: 'airline' (T1), 'airline_month' (T2), 'country' (T3). Returns rows + the totals printed in the sheet."""
    raw = _frame(path)
    head = _header_row(raw)
    title = _title(raw, head)
    rows, section, totals = [], None, {}
    if kind == "airline_month":
        # header: month names on the first header row at the start of each 4-column block, measures on the second
        months, cols = {}, []
        for j in range(raw.shape[1]):
            v = str(raw.iloc[head, j]).strip().upper()
            if v in MONTHS:
                months[j] = MONTHS.index(v) + 1
        if len(months) != 3:
            raise LayoutError(f"expected 3 month blocks, found {sorted(months.values())}")
        starts = sorted(months)
        for j in range(raw.shape[1]):
            h = " ".join(str(raw.iloc[head + 1, j]).upper().split())
            m = re.search(r"(PASSENGERS?|PAX|FREIGHT|FRT)\s+(TO|FROM)\s+INDIA", h)
            if m:
                blk = max(s for s in starts if s <= j)
                cols.append((j, months[blk], "pax" if m[1][0] == "P" else "freight_t", "To India" if m[2] == "TO" else "From India"))
        if len(cols) != 12:
            raise LayoutError(f"expected 12 measure columns, found {len(cols)}")
        first_data = head + 2
    else:
        cols0 = _cols(raw, head)
        if len(cols0) != 4:
            raise LayoutError(f"expected 4 measure columns, found {len(cols0)}")
        cols = [(j, None, m, d) for j, m, d in cols0]
        first_data = head + 1
    name_col = 1
    for i in range(first_data, len(raw)):
        first = str(raw.iloc[i, 0]).strip()
        name = " ".join(str(raw.iloc[i, name_col]).split()) if pd.notna(raw.iloc[i, name_col]) else ""
        if m := SECTION_RE.match(first):
            section = "Indian" if m[1].upper() == "DOMESTIC" else "Foreign"
        elif re.fullmatch(r"\d+", first):
            if kind == "country" and not name:
                continue
            vals = {(mo, meas, d): _num(raw.iloc[i, j]) for j, mo, meas, d in cols}
            rows.append((section, name, vals))
        elif first.upper().startswith("TOTAL"):
            key = "foreign" if "FOREIGN" in first.upper() and "DOMESTIC" not in first.upper() else "indian" if "DOMESTIC" in first.upper() and "FOREIGN" not in first.upper() else "all"
            totals[key] = {(mo, meas, d): _num(raw.iloc[i, j]) for j, mo, meas, d in cols}
    if not rows:
        raise LayoutError("no data rows")
    return {"kind": kind, "rows": rows, "totals": totals, "title": title, "cols": cols, "footnoted": footnotes(raw)}


def validate_named(p: dict) -> list[str]:
    """Rows must add up to the sheet's own section totals; section totals must add up to the grand total."""
    problems = []
    def total_of(sel):
        t = {}
        for sec, _, vals in p["rows"]:
            if sel is None or sec == sel:
                for k, v in vals.items():
                    t[k] = t.get(k, 0.0) + v
        return t
    def cmp(label, got, want):
        for k, w in want.items():
            sev = _severity(k[-2], got.get(k, 0.0), w, f"{label} {k[-2]} {k[-1]}")
            if sev:
                problems.append(sev)
                break
    if p["kind"] == "country":
        cmp("country total", total_of(None), p["totals"].get("all", {}))
        if not p["totals"]:
            problems.append("no TOTAL row")
    else:
        for sec, key in (("Indian", "indian"), ("Foreign", "foreign")):
            if key in p["totals"]:
                cmp(f"{sec} carriers", total_of(sec), p["totals"][key])
        if "all" in p["totals"]:
            cmp("all carriers", total_of(None), p["totals"]["all"])
        if not p["totals"]:
            problems.append("no TOTAL rows")
    return problems


def citypair(path) -> dict:
    """Table 4: block A = pairs to/from India (city 1 foreign, city 2 Indian); block B = Indian carriers entirely outside India.
    Block titles vary ('TABLE 4. ...', 'CITYPAIRWISE ...'), so a block starts at any title row mentioning CITYPAIR."""
    raw = _frame(path)
    blocks, cur, cols = [], None, None
    for i in range(len(raw)):
        first = str(raw.iloc[i, 0]).strip()
        up = re.sub(r"\s+", " ", first.upper())
        if "CITYPAIR" in up.replace("-", "").replace(" ", "") and pd.isna(raw.iloc[i, 1]):
            outside = "OUTSIDE" in up
            if cur is None or (outside != cur["outside"]):
                cur = {"outside": outside, "rows": [], "sub": None}
                blocks.append(cur)
        elif SNO_RE.match(first.replace(" ", "")):
            cols = _cols(raw, i)
            if len(cols) != 4:
                raise LayoutError(f"city-pair columns: {len(cols)}")
            if cur is None:
                cur = {"outside": False, "rows": [], "sub": None}
                blocks.append(cur)
        elif re.fullmatch(r"\d+", first) and cur is not None and cols:
            c1, c2 = " ".join(str(raw.iloc[i, 1]).split()), " ".join(str(raw.iloc[i, 2]).split())
            cur["rows"].append((c1, c2, {(meas, d): _num(raw.iloc[i, j]) for j, meas, d in cols}))
        elif up.startswith("SUB TOTAL") and cur is not None:
            vals = [raw.iloc[i, j] for j, _, _ in cols]
            cur["sub"] = None if _blank(vals) else {(meas, d): _num(raw.iloc[i, j]) for j, meas, d in cols}
        elif up.startswith("GRAND TOTAL"):
            return {"blocks": blocks, "grand": {(meas, d): _num(raw.iloc[i, j]) for j, meas, d in cols}, "footnoted": footnotes(raw)}
    return {"blocks": blocks, "grand": None, "footnoted": footnotes(raw)}


def validate_citypair(p: dict, ref: dict | None = None) -> list[str]:
    """`ref` = the same quarter's printed all-carrier totals from table 1 (or 3). Rows are checked against it first: when the rows agree
    with an independent table, a misprinted footer (sub/grand total) is a warning, not an error."""
    problems = []
    if not p["blocks"]:
        return ["no blocks"]
    tot, block_sums = {}, []
    for b in p["blocks"]:
        sm = {}
        for _, _, v in b["rows"]:
            for k, x in v.items():
                sm[k] = sm.get(k, 0.0) + x
        block_sums.append(sm)
        for k, x in sm.items():
            tot[k] = tot.get(k, 0.0) + x
    grand_ok = bool(p["grand"]) and all(_severity(k[0], tot.get(k, 0.0), w, "g") is None for k, w in p["grand"].items())
    ref_ok = False
    if ref:
        a = {}
        for b, sm in zip(p["blocks"], block_sums):
            if not b["outside"]:
                for k, x in sm.items():
                    a[k] = a.get(k, 0.0) + x
        sev = [_severity(k[0], a.get(k, 0.0), ref[(None,) + k], f"to/from-India rows vs table 1/3 {k[0]} {k[1]}") for k in a if (None,) + k in ref]
        bad = [x for x in sev if x]
        ref_ok = not [x for x in bad if not x.startswith("warn")]
        problems += bad
    footers_trusted = grand_ok or ref_ok
    for b, sm in zip(p["blocks"], block_sums):
        name = "outside-India block" if b["outside"] else "to/from-India block"
        if b["sub"] is None:
            problems.append(f"warn: {name} has no SUB TOTAL")
            continue
        for k, w in b["sub"].items():
            sev = _severity(k[0], sm.get(k, 0.0), w, f"{name} {k[0]} {k[1]} vs SUB TOTAL", soft_ok=footers_trusted)   # misprinted sub-total, grand total fine
            if sev:
                problems.append(sev)
                break
    if p["grand"]:
        for k, w in p["grand"].items():
            sev = _severity(k[0], tot.get(k, 0.0), w, f"GRAND TOTAL {k[0]} {k[1]}", soft_ok=ref_ok)
            if sev:
                problems.append(sev)
                break
    else:
        problems.append("warn: no GRAND TOTAL")
    return problems


# ---------------------------------------------------------------- long rows for publishing
def named_long(p: dict, year: int, quarter: int) -> pd.DataFrame:
    """Tables 1-3 -> one row per (name, month|quarter, direction) with pax and freight_t as columns."""
    recs = []
    for section, name, vals in p["rows"]:
        for (mo, meas, d), v in vals.items():
            recs.append((section, name, mo, d, meas, v))
    df = pd.DataFrame(recs, columns=["section", "name", "month", "direction", "meas", "v"])
    df["month"] = df["month"].astype("float")                         # NaN for quarterly tables; groupby must keep those rows
    df = df.groupby(["section", "name", "month", "direction", "meas"], dropna=False).v.sum().unstack("meas").reset_index()   # only real combinations
    df.columns.name = None
    first = QTR_MONTHS[quarter][0]
    df["period"] = [pd.Timestamp(year, int(m) if pd.notna(m) else first, 1) for m in df["month"]]
    return df.drop(columns="month")


def citypair_long(p: dict, year: int, quarter: int) -> pd.DataFrame:
    """Table 4 -> directed rows. In the to/from-India block city 1 is foreign and city 2 Indian ('to city 2' = flights to India)."""
    out = []
    for b in p["blocks"]:
        for c1, c2, v in b["rows"]:
            out.append((c1, c2, "Outside India" if b["outside"] else "To India", "outside" if b["outside"] else "india", v[("pax", "To India")], v[("freight_t", "To India")]))
            out.append((c2, c1, "Outside India" if b["outside"] else "From India", "outside" if b["outside"] else "india", v[("pax", "From India")], v[("freight_t", "From India")]))
    df = pd.DataFrame(out, columns=["origin", "destination", "direction", "scope", "pax", "freight_t"])
    df.insert(0, "period", pd.Timestamp(year, QTR_MONTHS[quarter][0], 1))
    return df
