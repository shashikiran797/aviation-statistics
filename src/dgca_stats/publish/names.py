"""Entity-name cleanup: the portal labels some years' files 'X excel'; display names are curated."""
import re

DISPLAY = {
    "AIR INDIA": "Air India", "AIR INDIA EXPRESS": "Air India Express", "INDIGO": "IndiGo", "SPICEJET": "SpiceJet",
    "GO AIR": "GoAir", "VISTARA": "Vistara", "AIR ASIA": "AirAsia India", "JET AIRWAYS": "Jet Airways",
    "JETLITE": "JetLite", "ALLIANCE AIR": "Alliance Air", "AIR DECCAN": "Air Deccan", "AIR COSTA": "Air Costa",
    "AIR PEGASUS": "Air Pegasus", "AIR CARNIVAL": "Air Carnival", "AIR ODISHA": "Air Odisha", "AIR HERITAGE": "Air Heritage",
    "STAR INDIA": "Star Air", "TRUJET": "TruJet", "ZOOM AIR": "Zoom Air", "BLUE DART": "Blue Dart Aviation",
    "QUIKJET CARGO": "Quikjet Cargo",
    "ALL SCHEDULED INDIAN AIRLINES": "All scheduled Indian airlines",
    "SCHEDULED NATIONAL AIRLINES": "Scheduled national airlines",
    "SCHEDULED PRIVATE AIRLINES": "Scheduled private airlines",
}
AGGREGATES = {"ALL SCHEDULED INDIAN AIRLINES", "SCHEDULED NATIONAL AIRLINES", "SCHEDULED PRIVATE AIRLINES"}


def canonical(entity: str) -> str:
    e = re.sub(r"\s+excel\s*$", "", entity.strip(), flags=re.I)
    return re.sub(r"\s+", " ", e).upper()


def display(key: str) -> str:
    return DISPLAY.get(key, key.title())


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
