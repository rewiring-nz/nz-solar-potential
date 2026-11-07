"""What kind of building a roof sits on: home, business, school, hospital...

A toggle for building types on the map: Homes, Schools, Hospitals,
Businesses, etc. Whatever the detailed building type categories you can find
access to."

WHAT IS ACTUALLY AVAILABLE, measured on the pilot's 1,270 outlines:

    LINZ `use`     Unknown 1,251  School 16  Supermarket 3
    LINZ `name`    six distinct, all schools and supermarkets

So LINZ names the civic and retail buildings it knows about -- schools,
hospitals, supermarkets, and a few dozen other categories nationally -- and
calls the other 98.5% "Unknown". For those, the only signal in hand is size,
and economics.js has drawn that line for weeks: a roof over 400 m2 or a
system over 100 kW is priced as a business. The same line is used here so the
map's "Businesses" and the economics' business tariff agree on which buildings
they mean.

The classification is deliberately coarse -- five types -- because a toggle
with thirty entries is a list, not a toggle, and because the honest
resolution of the underlying data is about five. It is also a single function
so the next source (a council zoning layer, OSM landuse) slots in as one more
clause with a measured effect, rather than as a second opinion somewhere else.
"""

# In display order. The frontend builds its toggle from this list, via
# assumptions.json, so adding a type here is the whole change.
BTYPES = [
    ("home",      "Homes"),
    ("business",  "Businesses"),
    ("industrial", "Industry"),
    ("school",    "Schools"),
    ("hospital",  "Hospitals"),
    ("community", "Community"),
]

# MIRRORS economics.js: biz_min_roof_m2 and biz_min_kw. Change both or the
# map and the money will call different buildings a business.
BIZ_MIN_ROOF_M2 = 400
BIZ_MIN_KW = 100

# LINZ `use` values, lower-cased substrings. The LINZ data dictionary lists
# these as free-text categories; matching on substrings survives the variants
# ("Hospital", "Hospital / Medical Centre").
_USE_RULES = [
    ("school", ("school", "kindergarten", "kura", "college", "university",
                "polytechnic", "education")),
    ("hospital", ("hospital", "medical", "health", "clinic", "hospice",
                  "rest home", "aged care")),
    ("community", ("church", "marae", "community", "hall", "library",
                   "museum", "gallery", "sport", "stadium", "gym", "pool",
                   "fire station", "police", "courthouse", "civic",
                   "council", "recreation", "club")),
    ("industrial", ("warehouse", "industrial", "factory", "depot",
                    "workshop", "storage")),
    ("business", ("supermarket", "shop", "retail", "mall", "commercial",
                  "office", "hotel",
                  "motel", "airport", "terminal", "bank", "market",
                  "petrol", "service station", "dairy", "restaurant", "cafe",
                  "bar", "brewery", "winery")),
]


# DISTRICT-PLAN ZONES (tools/fetch_zoning.py fetches each building's zone
# name). A zone says what may legally be built there. Industrial zones hold
# sheds, workshops and yards, which use about a third of the electricity per
# m2 of an office or shop (bill_tool.js BT_BIZ_KWH_M2), so they are their own
# type. Resort zones (Jacks Point, Millbrook, ...) are mostly houses around a
# lodge or clubhouse: the Jacks Point and Millbrook Resort zones held 1,485
# buildings called businesses in v72, with a median roof of 185-200 m2. They
# are "mixed", so size decides. Business Mixed Use and Settlements are mixed too.
INDUSTRIAL_ZONES = {
    "General Industrial and Service", "Coneburn Industrial", "Three Parks Business",
}
BUSINESS_ZONES = {
    "Airport", "Arrowtown Town Centre", "Queenstown Town Centre",
    "Wanaka Town Centre", "Local Shopping Centre", "Three Parks Commercial",
    "Active Sports and Recreation", "Community Purposes", "Civic Spaces",
    "Rural Visitor", "Te Pūtahi Ladies Mile",
}
HOME_ZONES = {
    "High Density Residential", "High Density Residential A",
    "Medium Density Residential", "Medium Density Residential A",
    "Large Lot Residential A", "Large Lot Residential B",
    "Arrowtown Residential Historic Management", "Rural Residential",
    "Rural Lifestyle", "Wakatipu Basin Lifestyle Precinct",
    "Wakatipu Basin Rural Amenity Zone", "Rural", "Gibbston Character",
}


def zone_class(name):
    """home / business / industrial for a zone name, else mixed (size decides)."""
    if name in INDUSTRIAL_ZONES:
        return "industrial"
    if name in BUSINESS_ZONES:
        return "business"
    if name in HOME_ZONES:
        return "home"
    return "mixed"


def classify(use=None, name=None, roof_m2=None, kwp=None, zone_class=None):
    """One of the BTYPES keys. A named LINZ use wins; then the council's
    district-plan zone (tools/fetch_zoning.py: a 300 m2 shop in the
    Queenstown Town Centre was a "home" by size alone, 18 Oct); size decides
    only where neither says, on the same line the economics use."""
    text = f"{use or ''} {name or ''}".lower()
    if text.strip() and "unknown" not in text.split():
        for btype, words in _USE_RULES:
            if any(w in text for w in words):
                return btype
    if zone_class in ("business", "home", "industrial"):
        return zone_class
    # No named use and no clear zone: size decides.
    if (roof_m2 or 0) >= BIZ_MIN_ROOF_M2 or (kwp or 0) >= BIZ_MIN_KW:
        return "business"
    return "home"
