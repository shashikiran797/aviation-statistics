"""Place-name normalisation for city-pair data.

DGCA changed its spelling conventions in Jan 2026 (mixed case, parenthetical qualifiers) and renamed/split some places.
Plain case/spacing differences merge automatically; renames and genuinely different airports are curated below.
`city` groups airports that serve one city so analyses can roll up (Mumbai + Navi Mumbai, the three Goa labels).
"""
import difflib
import re

# squashed raw name (upper, letters+digits only, parenthetical kept) -> (display name, city group)
CURATED = {
    "ALLAHABAD": ("Prayagraj", "Prayagraj"), "ALLAHABADPRAYAGRAJ": ("Prayagraj", "Prayagraj"),
    "CUDDAPAH": ("Kadapa", "Kadapa"), "CUDDAPAHKADAPA": ("Kadapa", "Kadapa"),
    "MANGALORE": ("Mangaluru", "Mangaluru"), "MANGALOREMANGALURU": ("Mangaluru", "Mangaluru"),
    "PONDICHERRY": ("Puducherry", "Puducherry"), "PONDICHERRYPUDUCHERRY": ("Puducherry", "Puducherry"),
    "VISHAKHAPATNAM": ("Visakhapatnam", "Visakhapatnam"), "VISAKHAPATNAM": ("Visakhapatnam", "Visakhapatnam"),
    "VISHAKHAPATNAMVISAKHAPATNAM": ("Visakhapatnam", "Visakhapatnam"),
    "TIRUCHIRAPALLY": ("Tiruchirappalli", "Tiruchirappalli"), "TIRUCHIRAPPALLI": ("Tiruchirappalli", "Tiruchirappalli"),
    "VIJAYWADA": ("Vijayawada", "Vijayawada"), "VIJAYAWADA": ("Vijayawada", "Vijayawada"),
    "BHATINDA": ("Bathinda", "Bathinda"), "BATHINDA": ("Bathinda", "Bathinda"),
    "HISSAR": ("Hisar", "Hisar"), "HISAR": ("Hisar", "Hisar"),
    "NASIK": ("Nashik", "Nashik"), "NASHIK": ("Nashik", "Nashik"),
    "SIMLA": ("Shimla", "Shimla"), "SHIMLA": ("Shimla", "Shimla"),
    "SOLAPUR": ("Solapur", "Solapur"), "SHOLAPUR": ("Solapur", "Solapur"),
    "DEHRADUN": ("Dehradun", "Dehradun"),
    "MUMBAIMUMBAI": ("Mumbai", "Mumbai"), "MUMBAINAVIMUMBAI": ("Navi Mumbai", "Mumbai"),
    # Goa has two airports since Jan 2023 (Dabolim, Mopa). DGCA published plain "Goa" for 2015-2025, "Dabolim" and "Mopa, Goa" in some
    # months, and explicit labels from Jan 2026. Plain "Goa" cannot be attributed to one airport after 2022, so it stays its own place;
    # the `city` group (Goa) is the reliable way to analyse Goa as a whole.
    "GOA": ("Goa (airport not specified)", "Goa"), "DABOLIM": ("Goa (Dabolim)", "Goa"), "GOADABOLIMSOUTHGOA": ("Goa (Dabolim)", "Goa"),
    "MOPAGOA": ("Goa (Mopa)", "Goa"), "GOAMOPANORTHGOA": ("Goa (Mopa)", "Goa"),
    # renames and labels that mean the same airport
    "COCHIN": ("Kochi", "Kochi"), "TRIVANDRUM": ("Thiruvananthapuram", "Thiruvananthapuram"),
    "GULBARGA": ("Kalaburagi", "Kalaburagi"), "KALABURAGIKARNATAKA": ("Kalaburagi", "Kalaburagi"), "BIDARKARNATAKA": ("Bidar", "Bidar"),
    "HOLLONGIITANAGAR": ("Itanagar", "Itanagar"), "ZERO": ("Ziro", "Ziro"), "PURNIA": ("Purnea", "Purnea"),
    "RAJKOTINTERNATIONALAIRPORT": ("Hirasar (Rajkot)", "Rajkot"),
    "HIRASARRAJKOT": ("Hirasar (Rajkot)", "Rajkot"), "RAJKOT": ("Rajkot", "Rajkot"),
    "HINDONAIRPORT": ("Hindon (Ghaziabad)", "Delhi NCR (Hindon)"), "GHAZIABAD": ("Hindon (Ghaziabad)", "Delhi NCR (Hindon)"),
}


def squash(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(raw).upper())


AIRPORT_SUFFIX = re.compile(r"\s+(international\s+)?airport\b", re.I)


def normalise(raw: str) -> tuple[str, str, str]:
    """-> (place_key, display, city). Looks up the raw label, then the label without ' Airport' / ' International Airport'.
    Unknown names keep their own spelling (title-cased if shouted)."""
    if squash(raw) in CURATED:
        disp, city = CURATED[squash(raw)]
    else:
        stripped = " ".join(AIRPORT_SUFFIX.sub("", str(raw)).split())
        if squash(stripped) in CURATED:
            disp, city = CURATED[squash(stripped)]
        else:
            disp = stripped.title() if stripped.isupper() else stripped
            city = re.sub(r"\s*\(.*?\)\s*", "", disp).strip() or disp
    return re.sub(r"[^a-z0-9]+", "_", disp.lower()).strip("_"), disp, city


def near_duplicates(display_names, threshold=0.88) -> list[tuple[str, str]]:
    """Distinct place names that look like spelling variants of each other: a signal that CURATED needs an entry."""
    keys = sorted({squash(re.sub(r"\(.*?\)", "", n)): n for n in display_names}.items())
    out = []
    for i, (a, na) in enumerate(keys):
        for b, nb in keys[i + 1:]:
            if abs(len(a) - len(b)) <= 2 and difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
                out.append((na, nb))
    return out
