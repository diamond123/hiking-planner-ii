MAX_ATTEMPTS = 8
MAX_PREFERENCE_ASKS = 1

RATE_LIMIT_PER_SECOND = 5

MAX_DATE_DAYS_AHEAD = 365
# Local (America/Los_Angeles) hour after which a same-day hike request is
# considered too late to have enough daylight/time left.
SAME_DAY_CUTOFF_HOUR = 16

# Redwood City/Redwood Shores - more geographically central to where this app's
# hike corpus actually is than downtown SF (only ~12 of ~289 documents are
# sfhikes/*), and avoids SF's distinctly cooler/foggier microclimate biasing the
# up-front weather check for what will likely be a non-SF candidate trail.
BAY_AREA_FALLBACK_LATLON = {"lat": 37.538612, "lon": -122.241279}

PREFERENCE_TOPICS = {
    "views": [
        "view", "views", "vista", "vistas", "scenic", "scenery", "panorama", "panoramic",
        "overlook", "overlooks", "lookout", "skyline", "cityscape",
        "waterfall", "waterfalls", "ocean", "coast", "coastal", "shoreline", "bay view",
        "lake", "sunset", "sunrise",
        "forest", "forested", "tree", "trees", "redwood", "redwoods",
        "meadow", "meadows", "wildflower", "wildflowers", "canyon",
    ],
    "difficulty": ["easy", "moderate", "hard", "challenging", "beginner", "strenuous"],
    "elevation_gain": ["elevation", "gain", "climb", "steep", "vert"],
    "distance": ["mile", "miles", "km", "kilometer", "distance", "short", "long"],
}

PREFERENCE_TOPIC_LABELS = {
    "views": "the kind of views you want",
    "difficulty": "your preferred difficulty level",
    "elevation_gain": "your elevation gain preference",
    "distance": "your preferred total distance",
}

ALL_PREFERENCE_TOPICS = set(PREFERENCE_TOPICS)

# Order missing preference topics are asked about in, when more than one is
# still missing. Not alphabetical — a deliberate, product-chosen order.
PREFERENCE_TOPIC_ORDER = ["distance", "views", "difficulty", "elevation_gain"]

NON_CA_STATE_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
}

NON_CA_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

NON_US_COUNTRY_KEYWORDS = {
    "canada",
    "mexico",
    "united kingdom",
    "england",
    "france",
    "germany",
    "italy",
    "spain",
    "china",
    "japan",
    "india",
    "australia",
    "new zealand",
    "brazil",
    "argentina",
    "chile",
}
