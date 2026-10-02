"""Paths and site constants. Everything is overridable via env for tests."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("DGCA_ROOT", Path(__file__).resolve().parents[2]))
DATA = Path(os.environ.get("DGCA_DATA", ROOT / "data"))
RAW = DATA / "raw"
HTML_CACHE = DATA / "raw" / "_html"
QUARANTINE = DATA / "quarantine"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
MANIFEST = DATA / "manifest.parquet"
SITE_DATA = ROOT / "web" / "data"

PORTAL = "https://www.dgca.gov.in/digigov-portal/"
S3 = "https://public-prd-dgca.s3.ap-south-1.amazonaws.com/"
STATS_BASE = "jsp/dgca/InventoryList/dataReports/aviationDataStatistics/airTransport/domestic/yearly/"
STATS_INDEX = PORTAL + STATS_BASE + "index.html"
SECTION = "air_transport_statistics"

USER_AGENT = "dgca-stats/0.1 (research; +https://github.com/; contact via repo owner)"
MIN_DELAY_S = float(os.environ.get("DGCA_DELAY", "0.3"))  # politeness between requests
HTML_MAX_AGE_S = int(os.environ.get("DGCA_HTML_MAX_AGE", str(24 * 3600)))
