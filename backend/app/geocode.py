import logging

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Bay Area bounding box, used to bias/constrain geocoding results.
BAY_AREA_VIEWBOX = "-123.3,38.5,-121.0,36.8"


def geocode_location(location_text: str) -> dict | None:
    """Resolve free-text location to {"lat": float, "lon": float}, or None if it can't be resolved."""
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
            timeout=5,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])}
    except Exception:
        logger.exception("geocoding failed for %r", location_text)
        return None
