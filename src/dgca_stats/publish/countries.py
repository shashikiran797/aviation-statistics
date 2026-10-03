"""Country-name harmonisation for the quarterly international tables (46 quarters, a few typos and footnote markers)."""
import re

CURATED = {"UNITEDARABEMIRATES": "United Arab Emirates", "NEWZELAND": "New Zealand", "BURMA": "Myanmar (Burma)", "KOREA": "Korea",
           "UNITEDKINGDOM": "United Kingdom", "UNITEDSTATES": "United States", "BRUNEIDARUSSALAM": "Brunei Darussalam"}


def normalise(raw: str) -> tuple[str, str]:
    """-> (country_key, display). Footnote asterisks ('UNITED ARAB EMIRATES*') and case differences collapse."""
    name = " ".join(str(raw).replace("*", " ").split())
    k = re.sub(r"[^A-Z0-9]", "", name.upper())
    disp = CURATED.get(k) or (name.title() if name.isupper() else name)
    return re.sub(r"[^a-z0-9]+", "_", disp.lower()).strip("_"), disp
