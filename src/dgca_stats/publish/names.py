"""Entity-name cleanup: the portal labels some years' files 'X excel'; display names are curated."""
import re

DISPLAY = {
    "AIR INDIA": "Air India", "AIR INDIA EXPRESS": "Air India Express", "INDIGO": "IndiGo", "SPICEJET": "SpiceJet",
    "GO AIR": "GoAir", "VISTARA": "Vistara", "AIR ASIA": "AirAsia India / AIX Connect", "JET AIRWAYS": "Jet Airways",
    "JETLITE": "JetLite", "ALLIANCE AIR": "Alliance Air", "AIR DECCAN": "Air Deccan", "AIR COSTA": "Air Costa",
    "AIR PEGASUS": "Air Pegasus", "AIR CARNIVAL": "Air Carnival", "AIR ODISHA": "Air Odisha", "AIR HERITAGE": "Air Heritage",
    "TRUJET": "TruJet", "ZOOM AIR": "Zoom Air", "BLUE DART": "Blue Dart Aviation",
    "QUIKJET CARGO": "Quikjet Cargo", "STAR AIR": "Star Air", "AKASA AIR": "Akasa Air", "FLY 91": "Fly91", "FLY BIG": "Fly Big",
    "INDIA ONE AIR": "IndiaOne Air", "PAWAN HANS": "Pawan Hans", "AIR TAXI": "Air taxi operators",
    "TOTAL DOMESTIC": "Total domestic (monthly series)", "TOTAL INTERNATIONAL": "Total international (monthly series)",
    "ALL SCHEDULED INDIAN AIRLINES": "All scheduled Indian airlines",
    "SCHEDULED NATIONAL AIRLINES": "Scheduled national airlines",
    "SCHEDULED PRIVATE AIRLINES": "Scheduled private airlines",
}
AGGREGATES = {"ALL SCHEDULED INDIAN AIRLINES", "SCHEDULED NATIONAL AIRLINES", "SCHEDULED PRIVATE AIRLINES", "TOTAL DOMESTIC", "TOTAL INTERNATIONAL"}
ALIASES = {  # keyed on the upper-cased, letters+digits-only form of the name
    "BLUEDART": "BLUE DART", "BLUEDARTCLICK": "BLUE DART", "STARINDIA": "STAR AIR", "INDIAONE": "INDIA ONE AIR", "INDIAONEAIR": "INDIA ONE AIR",
    "FLY91": "FLY 91", "AIRINDIAEXPRESSLTD": "AIR INDIA EXPRESS", "GOAIR": "GO AIR", "AKASA": "AKASA AIR", "ALLIANCE": "ALLIANCE AIR",
    "QUICKJETCARGO": "QUIKJET CARGO", "TRUJET": "TRUJET", "DECCANAIR": "AIR DECCAN", "AIRDECCAN": "AIR DECCAN",
    "AIXCONNECT": "AIR ASIA", "AIRASIAINDIA": "AIR ASIA",   # AirAsia India was renamed AIX Connect (2023-24): one continuing series
}


def canonical(entity: str) -> str:
    e = re.sub(r"\s+excel\s*$", "", entity.strip(), flags=re.I)
    e = re.sub(r"\s+", " ", e).upper()
    return ALIASES.get(re.sub(r"[^A-Z0-9]", "", e), e)


def display(key: str) -> str:
    return DISPLAY.get(key, key.title())


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
