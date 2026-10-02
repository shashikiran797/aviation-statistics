"""Parse DGCA 'monthly traffic and operating statistics' xlsx tables (ICAO ATR Form A layout) into tidy rows."""
import re

import pandas as pd

MONTHS = "APR MAY JUN JUL AUG SEP OCT NOV DEC JAN FEB MAR".split()
# positional columns of the ICAO form layout; labels are validated against the header so a layout change fails loudly
COLS = ["period", "departures", "hours", "kms_thousand", "pax", "pax_km_million", "ask_million", "pax_load_factor",
        "freight_t", "mail_t", "cargo_t", "tkm_pax_million", "tkm_freight_million", "tkm_mail_million", "tkm_total_million",
        "atk_million", "weight_load_factor"]
ADDITIVE = [c for c in COLS[1:] if c not in ("pax_load_factor", "weight_load_factor")]
HEADER_EXPECT = {1: "DEPARTURES", 4: "PASSENGERS", 6: "AVAILABLE SEAT", 7: "LOAD FACTOR", 16: "LOAD FACTOR"}


class LayoutError(ValueError):
    pass


def _month(label) -> str | None:
    """'APR', 'April', or a real date (2018-04-01) -> 'APR'. Anything else (headers, year totals) -> None."""
    if hasattr(label, "strftime"):
        return label.strftime("%b").upper()
    s = str(label).strip().upper()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
        return pd.Timestamp(s[:10]).strftime("%b").upper()
    s = s[:3]
    return s if s in MONTHS else None


def _is_year_label(label, fy: str) -> bool:
    if _month(label):
        return False
    s = re.sub(r"\s+", "", str(label))
    y0 = fy[:4]
    return bool(re.fullmatch(rf"{y0}-(\d{{2}}|\d{{4}})", s)) or s in (fy, f"TOTAL")


def read(path, fy: str) -> tuple[pd.DataFrame, pd.Series | None]:
    """Return (12 monthly rows, annual row or None). Raises LayoutError when the sheet does not look as expected."""
    raw = pd.read_excel(path, header=None)
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.shape[1] < len(COLS):
        raise LayoutError(f"expected >= {len(COLS)} columns, found {raw.shape[1]}")
    raw = raw.iloc[:, : len(COLS)].reset_index(drop=True)
    raw.columns = COLS
    head = " ".join(raw.iloc[:7].astype(str).agg(" ".join, axis=1)).upper()
    for needle in ("DEPARTURES", "PASSENGERS", "AVAILABLE SEAT", "LOAD FACTOR"):
        if needle not in head:
            raise LayoutError(f"header missing '{needle}'")
    months = raw[raw["period"].map(_month).notna()].copy()
    months["month"] = months["period"].map(_month)
    months = months.drop_duplicates("month")
    annual_rows = raw[raw["period"].map(lambda v: _is_year_label(v, fy))]
    annual = annual_rows.iloc[0] if len(annual_rows) else None
    if len(months) != 12:
        raise LayoutError(f"expected 12 month rows, found {len(months)}: {sorted(months['month'])}")
    for c in COLS[1:]:
        months[c] = pd.to_numeric(months[c], errors="coerce")
    return months.drop(columns="period").reset_index(drop=True), annual


def validate(months: pd.DataFrame, annual: pd.Series | None) -> list[str]:
    """Hard errors only; empty list means the file is accepted. Soft warnings are returned with a 'warn:' prefix."""
    problems = []
    if months[["departures", "pax"]].isna().all().any():
        problems.append("departures/pax entirely empty")
    if (months[ADDITIVE].fillna(0) < 0).any().any():
        problems.append("negative values in additive column")
    if annual is None:
        problems.append("warn: no annual row to reconcile against")
        return problems
    for c in ADDITIVE:
        a = pd.to_numeric(annual[c], errors="coerce")
        s = months[c].sum(min_count=1)
        if pd.isna(a) or pd.isna(s):
            continue
        tol = max(1.0, abs(a) * 2e-3)
        if abs(a - s) > tol:
            problems.append(f"{c}: months sum {s:.4g} != annual {a:.4g}")
    return problems


def tidy(months: pd.DataFrame, entity: str, fy: str, segment: str, source: dict) -> pd.DataFrame:
    df = months.copy()
    fy_start = int(fy[:4])
    df.insert(0, "entity", entity)
    df.insert(1, "fy", fy)
    df.insert(2, "segment", segment)
    for k, v in source.items():
        df[k] = v
    return df
