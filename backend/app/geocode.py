import logging
import time

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
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
