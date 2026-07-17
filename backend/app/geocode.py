import logging
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


def geocode_location(location_text: str) -> dict | None:
    """Resolve free-text location to {"lat": float, "lon": float}, or None if it can't be resolved."""
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
            return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])}
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
