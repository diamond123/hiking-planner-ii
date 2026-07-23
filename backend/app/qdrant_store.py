import random
import logging
logger = logging.getLogger(__name__)
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, GeoBoundingBox, GeoPoint, MatchAny

from app.config import settings
from app.db import get_sources_with_prefix

_client = QdrantClient(path=settings.qdrant_full_path)
_embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)

# Fallback only - used if location_latlon has no "bbox" (e.g. a manually-
# constructed dict rather than one from geocode_location()). A plain square in
# degrees rather than the latitude-adjusted math in geocode.py's
# _bbox_around_point - this path is a safety net, not expected to be hit in
# practice, so precise longitude scaling isn't worth it here.
FALLBACK_BBOX_HALF_DEGREES = 0.2  # ~14 miles


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
        bbox = location_latlon.get("bbox")
        if not bbox:
            lat0, lon0 = location_latlon["lat"], location_latlon["lon"]
            bbox = {
                "south": lat0 - FALLBACK_BBOX_HALF_DEGREES,
                "north": lat0 + FALLBACK_BBOX_HALF_DEGREES,
                "west": lon0 - FALLBACK_BBOX_HALF_DEGREES,
                "east": lon0 + FALLBACK_BBOX_HALF_DEGREES,
            }
        logger.info(f"search_chunk: query_text={query_text}, location_latlon={location_latlon}, bbox={bbox}")
        must.append(
            FieldCondition(
                key="metadata.location",
                geo_bounding_box=GeoBoundingBox(
                    top_left=GeoPoint(lat=bbox["north"], lon=bbox["west"]),
                    bottom_right=GeoPoint(lat=bbox["south"], lon=bbox["east"]),
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
