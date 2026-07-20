import logging
import math
import re
import time

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Bay Area bounding box, used to bias/constrain geocoding results.
BAY_AREA_VIEWBOX = "-123.3,38.5,-121.0,36.8"

# Nominatim is a free, shared public service that occasionally throttles or
# times out transiently — retry once before giving up, so a momentary blip
# doesn't get reported to the user as "couldn't recognize that location".
GEOCODE_MAX_ATTEMPTS = 2
GEOCODE_RETRY_BACKOFF_SECONDS = 1.0
GEOCODE_REQUEST_TIMEOUT_SECONDS = 8

EARTH_RADIUS_MILES = 3958.8

# Clamps for the per-location geo search radius (see _radius_miles_from_bbox
# below) - MIN keeps a specific match (a park, a street address) from
# searching an unreasonably tiny area, MAX keeps a broad match (a whole city
# or region) from pulling in candidates from well outside the Bay Area.
# DEFAULT is used only if Nominatim's response is ever missing a usable
# bounding box - the old fixed radius, kept as a safety net.
MIN_GEO_RADIUS_MILES = 6
MAX_GEO_RADIUS_MILES = 25
DEFAULT_GEO_RADIUS_MILES = 15

# "East Bay"/"South Bay"/"North Bay"/"Peninsula" are colloquial multi-city Bay
# Area sub-regions with no corresponding OSM polygon, so Nominatim can't return
# a real match for them - it falls back to scoring unrelated same-named point
# features (e.g. a literal road named "East Bay" near Stockton, a "South Bay"
# water inlet in SF, "Peninsula Temple Sholom" in Burlingame), each with a
# sub-mile bounding box. That silently produces a MIN_GEO_RADIUS_MILES search
# centered on the wrong place. Hardcode centers/radii for these names instead
# of trusting Nominatim for them.
#
# `radius_miles` alone isn't enough to keep candidates within the right
# region, though: the Bay's narrow crossings (Dumbarton, San Mateo bridges)
# put East Bay parks within ~15-19mi of these centers, well inside a radius
# sized to also reach genuinely-distant same-region hikes (e.g. the Santa
# Cruz Mountains preserves that make up much of "South Bay"'s own document
# set are 15-20mi from downtown San Jose in the *other* direction) - so
# shrinking the radius to exclude cross-bay contamination would gut real
# same-region results rather than fix the problem. `source_prefix` is a much
# more precise filter for this: the document corpus was scraped from a site
# organized into exactly these region folders (see documents.db `source`,
# e.g. "eastbayhikes%2Fpleasanton.html") - search_qdrant uses it as a hard
# `metadata.source` filter, keyed by the source site's own regional
# categorization rather than straight-line distance.
BAY_AREA_REGION_OVERRIDES: dict[str, dict[str, float | str]] = {
    "east bay": {"lat": 37.8272, "lon": -122.0538, "radius_miles": 20.0, "source_prefix": "eastbayhikes"},
    "south bay": {"lat": 37.3382, "lon": -121.8863, "radius_miles": 20.0, "source_prefix": "southbayhikes"},
    "north bay": {"lat": 38.2919, "lon": -122.4580, "radius_miles": 25.0, "source_prefix": "northbayhikes"},
    "peninsula": {"lat": 37.4852, "lon": -122.2364, "radius_miles": 15.0, "source_prefix": "southbayhikes"},
}

_REGION_ALIAS_LEADING_THE_RE = re.compile(r"^the\s+", re.IGNORECASE)
_REGION_ALIAS_STATE_COUNTRY_RE = re.compile(
    r",?\s*(?:california|ca|usa|u\.s\.a\.?|united states|us)\b", re.IGNORECASE
)
_REGION_ALIAS_TRAILING_WORD_RE = re.compile(r"\s+(?:area|region)\b", re.IGNORECASE)


def _match_bay_area_region(location_text: str) -> dict[str, float] | None:
    """Match location_text against BAY_AREA_REGION_OVERRIDES, tolerant of
    normalization already applied upstream (a leading "the ", an appended
    ", California, USA", a trailing "Area"/"Region"). Requires the whole
    (stripped) string to match a known alias, not just contain it as a
    substring - otherwise a real, more specific place like "Point Reyes
    Peninsula" would be wrongly swallowed by the generic "peninsula" override.
    """
    stripped = _REGION_ALIAS_LEADING_THE_RE.sub("", location_text.strip())
    stripped = _REGION_ALIAS_STATE_COUNTRY_RE.sub("", stripped)
    stripped = _REGION_ALIAS_TRAILING_WORD_RE.sub("", stripped)
    return BAY_AREA_REGION_OVERRIDES.get(stripped.strip(" ,").lower())


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _radius_miles_from_bbox(boundingbox: list[str] | None) -> float:
    """Scale the geo search radius to how broad the matched location is - a
    specific match (a park, a street address) should search a small area
    around it; a broad match (a whole city or region) should cast a wider
    net so a "somewhere in San Jose"-style request isn't limited to one
    corner of it. Nominatim's `boundingbox` (south, north, west, east) is a
    free, already-present proxy for a match's real-world extent - its
    diagonal is tiny for a point-like match and large for a city/region one.
    """
    if not boundingbox or len(boundingbox) != 4:
        return DEFAULT_GEO_RADIUS_MILES
    south, north, west, east = (float(x) for x in boundingbox)
    diagonal_miles = _haversine_miles(south, west, north, east)
    return max(MIN_GEO_RADIUS_MILES, min(MAX_GEO_RADIUS_MILES, diagonal_miles))


def geocode_location(location_text: str) -> dict | None:
    """Resolve free-text location to {"lat": float, "lon": float, "radius_miles": float},
    or None if it can't be resolved. `radius_miles` scales with how broad the matched
    location is (see _radius_miles_from_bbox) and is meant for the Qdrant geo_radius filter.
    """
    region_override = _match_bay_area_region(location_text)
    if region_override is not None:
        return dict(region_override)

    for attempt in range(1, GEOCODE_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": location_text,
                    "format": "json",
                    "limit": 1,
                    "viewbox": BAY_AREA_VIEWBOX,
                    "bounded": 0,
                },
                headers={"User-Agent": "hiking-planner/0.1 (local demo app)"},
                timeout=GEOCODE_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            results = resp.json()
            if not results:
                return None
            result = results[0]
            return {
                "lat": float(result["lat"]),
                "lon": float(result["lon"]),
                "radius_miles": _radius_miles_from_bbox(result.get("boundingbox")),
            }
        except Exception:
            if attempt < GEOCODE_MAX_ATTEMPTS:
                logger.warning(
                    "geocoding attempt %d/%d failed for %r, retrying",
                    attempt, GEOCODE_MAX_ATTEMPTS, location_text, exc_info=True,
                )
                time.sleep(GEOCODE_RETRY_BACKOFF_SECONDS)
            else:
                logger.exception(
                    "geocoding failed for %r after %d attempts", location_text, GEOCODE_MAX_ATTEMPTS
                )
                return None


# Nominatim's `address.road` field is populated for any nearest named "highway"
# way, including footpaths/cycleways through parks and open space - not just
# streets with real house-number addressing. Trailheads deep in a regional park
# (e.g. Quarry Lakes) often have no real street nearby, so the "road" Nominatim
# reports is literally the trail itself (e.g. "San Francisco Bay Trail"). Feeding
# that through house-number-style formatting produces a string that looks like a
# mailing address but isn't one. `address` doesn't expose the road segment's own
# class/type (only the top-matched result's), so a name keyword check backstops
# the class/type check for cases like a parking lot (class=amenity) that sits on
# a trail rather than a street.
_TRAIL_HIGHWAY_TYPES = {"path", "footway", "cycleway", "bridleway", "track", "steps", "pedestrian"}
_TRAIL_NAME_KEYWORDS = ("trail", "path")


def _is_trail_road(result: dict, road: str) -> bool:
    if result.get("class") == "highway" and result.get("type") in _TRAIL_HIGHWAY_TYPES:
        return True
    return any(kw in road.lower() for kw in _TRAIL_NAME_KEYWORDS)


def _format_reverse_geocode_address(result: dict) -> str | None:
    address = result.get("address")
    if not address:
        return result.get("display_name") or None

    parts = []
    road = address.get("road")
    if road and _is_trail_road(result, road):
        # No real street here - fall back to the trail/park name itself (prefer
        # the matched feature's own name, e.g. a park polygon, over the trail
        # segment name) instead of fabricating a house-numbered-style address.
        parts.append(result.get("name") or road)
    elif road:
        house_number = address.get("house_number")
        parts.append(f"{house_number} {road}" if house_number else road)

    city = address.get("city") or address.get("town") or address.get("village") or address.get("hamlet")
    if city:
        parts.append(city)

    # This app is scoped to Bay Area/California locations only, so "California"
    # is the only state Nominatim will ever return here - no need for a full
    # state-name-to-abbreviation table.
    state = address.get("state")
    if state:
        parts.append("CA" if state == "California" else state)

    postcode = address.get("postcode")
    if postcode:
        parts.append(postcode)

    return ", ".join(parts) if parts else (result.get("display_name") or None)


def reverse_geocode_latlon(lat: float, lon: float) -> str | None:
    """Resolve {lat, lon} to a human-readable address string, or None if it can't be resolved."""
    for attempt in range(1, GEOCODE_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                NOMINATIM_REVERSE_URL,
                params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
                headers={"User-Agent": "hiking-planner/0.1 (local demo app)"},
                timeout=GEOCODE_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            result = resp.json()
            return _format_reverse_geocode_address(result)
        except Exception:
            if attempt < GEOCODE_MAX_ATTEMPTS:
                logger.warning(
                    "reverse geocoding attempt %d/%d failed for %r,%r, retrying",
                    attempt, GEOCODE_MAX_ATTEMPTS, lat, lon, exc_info=True,
                )
                time.sleep(GEOCODE_RETRY_BACKOFF_SECONDS)
            else:
                logger.exception(
                    "reverse geocoding failed for %r,%r after %d attempts", lat, lon, GEOCODE_MAX_ATTEMPTS
                )
                return None
