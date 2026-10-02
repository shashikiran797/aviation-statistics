"""Parse DGCA calendar-year 'monthly statistics by operator' xlsx (2015-onward series).

Same ICAO Form A columns as the fiscal-year tables, but: JAN..DEC rows, several blocks per sheet (scheduled domestic,
scheduled international, non-scheduled...), partial current year, and capacity/work columns published in THOUSANDS
(older tables use MILLIONS). Units are detected from each column's header text and normalised to millions.
"""
import re

import pandas as pd

from .monthly import ADDITIVE, COLS, LayoutError, _month

UNIT_COLS = {"pax_km_million": 5, "ask_million": 6, "tkm_pax_million": 11, "tkm_freight_million": 12, "tkm_mail_million": 13,
             "tkm_total_million": 14, "atk_million": 15}
SEGMENT_RE = [(re.compile(r"NON[\s-]*SCHEDULED", re.I), None), (re.compile(r"INTERNATIONAL", re.I), "international"),
              (re.compile(r"DOMESTIC", re.I), "domestic")]


def _scale(header: str) -> float:
    h = header.upper()
    if "THOUSAND" in h:
        return 1e-3
    if "MILLION" in h:
        return 1.0
    raise LayoutError(f"cannot tell units from header {header!r}")


def read_blocks(path, filename_year: int | None = None) -> list[dict]:
    raw = pd.read_excel(path, header=None).dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    if raw.shape[1] < len(COLS):
        raise LayoutError(f"expected >= {len(COLS)} columns, found {raw.shape[1]}")
    raw = raw.iloc[:, : len(COLS)]
    starts = [i for i, v in enumerate(raw[0]) if str(v).strip().upper() == "MONTH"]
    if not starts:
        raise LayoutError("no MONTH header row found")
    blocks = []
    for n, h in enumerate(starts):
        title_cells = " ".join(str(v) for v in raw.iloc[max(0, h - 1)].dropna()) if h else ""
        # the title row sits above MONTH; in older files it is one long cell, in newer ones a year + a subtitle
        seg = None
        for rx, s in SEGMENT_RE:
            if rx.search(title_cells):
                seg = s
                break
        if seg is None and not title_cells.strip():
            continue
        title_year = int(m[1]) if (m := re.search(r"\b(20\d\d|19\d\d)\b", title_cells)) else None
        year = filename_year or title_year      # the file's own year is authoritative; some sheets mislabel a block
        end = next((i for i in range(h + 1, len(raw)) if str(raw.iloc[i, 0]).strip().upper() == "TOTAL"), None)
        if end is None:
            raise LayoutError("block without TOTAL row")
        head_text = {c: " ".join(str(raw.iloc[r, c]) for r in range(h, h + 3) if r < len(raw)) for c in range(len(COLS))}
        scale = {c: _scale(head_text[i]) for c, i in UNIT_COLS.items()}
        body = raw.iloc[h + 1 : end].copy()
        body.columns = COLS
        body["m"] = body["period"].map(_month)
        body = body[body["m"].notna()].copy()
        for c in COLS[1:]:
            body[c] = pd.to_numeric(body[c], errors="coerce")
        for c, f in scale.items():
            body[c] = body[c] * f
        total = raw.iloc[end].copy()
        total.index = COLS
        total = total.drop("period")
        for c in COLS[1:]:
            total[c] = pd.to_numeric(total[c], errors="coerce")
        for c, f in scale.items():
            total[c] = total[c] * f
        blocks.append({"segment": seg, "year": year, "title_year": title_year, "provisional": "PROVISIONAL" in title_cells.upper() or "PROVISIONAL" in " ".join(map(str, raw.iloc[h : h + 3, 2].dropna())).upper(),
                       "months": body, "total": total})
    return blocks


def tidy_block(b: dict) -> pd.DataFrame:
    """Rows with data only (future months are blank); adds a real `period` date."""
    d = b["months"]
    d = d[d[["departures", "pax"]].notna().any(axis=1)].copy()
    if d.empty:
        return d
    if b["year"] is None:
        raise LayoutError("year not found")
    mon = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
    d["period"] = [pd.Timestamp(b["year"], mon[m], 1) for m in d["m"]]
    d["provisional"] = b["provisional"]
    return d.drop(columns=["m", "pax_load_factor", "weight_load_factor"]).reset_index(drop=True)


def validate_block(b: dict, rows: pd.DataFrame) -> list[str]:
    problems = []
    if rows.empty:
        return ["no data rows"]
    for c in ADDITIVE:
        a, s = b["total"].get(c), rows[c].sum(min_count=1)
        if pd.isna(a) or pd.isna(s):
            continue
        if abs(a - s) > max(1.0, abs(a) * 2e-3):
            problems.append(f"{c}: months sum {s:.6g} != total {a:.6g}")
    if (rows[ADDITIVE].fillna(0) < 0).any().any():
        problems.append("negative values")
    return problems


def read(path, filename_year=None, segments=("domestic", "international")):
    """-> (rows for requested scheduled segments, list of problems). One tidy frame per segment, concatenated."""
    out, problems, saw = [], [], False
    for b in read_blocks(path, filename_year):
        if b["segment"] not in segments:
            continue
        saw = True
        t = tidy_block(b)
        if t.empty:                       # e.g. operator did not fly that year: valid, contributes no rows
            continue
        problems += [f"{b['segment']} {p}" for p in validate_block(b, t)]
        t.insert(0, "segment", b["segment"])
        out.append(t)
    if not saw:
        raise LayoutError("no scheduled domestic/international block")
    if not out:
        return pd.DataFrame(columns=["segment", "period", *COLS[1:]]), problems
    return pd.concat(out, ignore_index=True), problems
