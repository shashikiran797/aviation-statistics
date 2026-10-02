"""Dry run: DGCA monthly airline table (xlsx, 2-row header) -> tidy CSV."""
import re, sys, urllib.parse, urllib.request, pandas as pd

S3 = "https://public-prd-dgca.s3.ap-south-1.amazonaws.com/InventoryList/dataReports/aviationDataStatistics/airTransport/domestic/yearly"
COLS = ["month","departures","hours","kms_thousand","pax","pax_km_million","ask_million","pax_load_factor",
        "freight_t","mail_t","cargo_t","tkm_pax","tkm_freight","tkm_mail","tkm_total","atk_million","weight_load_factor"]
MONTHS = "APR MAY JUN JUL AUG SEP OCT NOV DEC JAN FEB MAR".split()

def fetch(year_dir, airline, table):
    url = f"{S3}/{year_dir}/Normal/{urllib.parse.quote(airline)}/excel/{table}.xlsx"
    path = f"/tmp/{airline.replace(' ','_')}_{table}.xlsx"
    urllib.request.urlretrieve(url, path)
    return path

def parse(path, airline, fy):
    raw = pd.read_excel(path, header=None)
    raw = raw.iloc[:, :17]; raw.columns = COLS
    raw["month"] = raw["month"].astype(str).str.strip().str.upper()
    df = raw[raw["month"].isin(MONTHS + [fy])].copy()
    df["month"] = df["month"].replace({fy: "TOTAL"})
    for c in COLS[1:]: df[c] = pd.to_numeric(df[c], errors="coerce")
    df.insert(0, "airline", airline); df.insert(1, "fy", fy); df.insert(2, "segment", "domestic")
    return df
