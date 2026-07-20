import random
import logging
logger = logging.getLogger(__name__)
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, GeoPoint, GeoRadius, MatchAny

from app.config import settings
from app.db import get_sources_with_prefix

_client = QdrantClient(path=settings.qdrant_full_path)
_embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)

MILES_TO_METERS = 1609.34
# Fallback only - used if location_latlon has no "radius_miles" (e.g. a
# manually-constructed dict rather than one from geocode_location()). The
# real per-location value is computed in geocode.py from how broad the
# geocoded match is - see _radius_miles_from_bbox there.
GEO_RADIUS_MILES = 15


def search_chunk(
    query_text: str,
    excluded_sources: list[str] | None = None,
    location_latlon: dict | None = None,
) -> dict | None:
    """Return the single best-matching chunk payload, or None if nothing matches."""
    vector = _embeddings.embed_query(query_text)

    must_not = []
    if excluded_sources:
        must_not.append(FieldCondition(key="metadata.source", match=MatchAny(any=excluded_sources)))

    must = []
    if location_latlon:
        radius_miles = location_latlon.get("radius_miles", GEO_RADIUS_MILES)
        # radius_miles = max(radius_miles, GEO_RADIUS_MILES)  # never search a smaller area than the fallback
        logger.info(f"search_chunk: query_text={query_text}, location_latlon={location_latlon}, radius_miles={radius_miles}")
        must.append(
            FieldCondition(
                key="metadata.location",
                geo_radius=GeoRadius(
                    center=GeoPoint(lat=location_latlon["lat"], lon=location_latlon["lon"]),
                    radius=radius_miles * MILES_TO_METERS,
                ),
            )
        )
        source_prefix = location_latlon.get("source_prefix")
        if source_prefix:
            # Set by a BAY_AREA_REGION_OVERRIDES match (geocode.py) - the geo_radius
            # above alone can't keep e.g. "South Bay" from reaching across the Bay's
            # narrow crossings into East Bay parks, since the radius also has to be
            # wide enough to cover genuinely-distant same-region hikes. This filters
            # to the source site's own regional folder instead of trusting distance.
            prefixed_sources = get_sources_with_prefix(source_prefix)
            if prefixed_sources:
                must.append(FieldCondition(key="metadata.source", match=MatchAny(any=prefixed_sources)))

    query_filter = Filter(must=must or None, must_not=must_not or None)

    results = _client.query_points(
        collection_name=settings.qdrant_collection_name,
        query=vector,
        query_filter=query_filter,
        limit=10,
    ).points

    if not results:
        return None
    
    # L3 Norm
    scores = [result.score ** 3 for result in results]
    # sources = [result.payload.get("metadata", {}).get("source", "unknown") for result in results]
    # formatted_scores = [(source, f"{score:.3f}") for source, score in zip(sources, scores)]
    # logger.info(f"scores={formatted_scores}")
    total_score = sum(scores)
    
    choice = random.uniform(0, total_score)
    for i, score in enumerate(scores):
        choice -= score
        if choice <= 0:
            bestIndex = i
            break
    else:
        bestIndex = -1

    return results[bestIndex].payload if bestIndex >= 0 else None
