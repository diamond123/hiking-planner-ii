import random
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, GeoPoint, GeoRadius, MatchAny

from app.config import settings

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
        must.append(
            FieldCondition(
                key="metadata.location",
                geo_radius=GeoRadius(
                    center=GeoPoint(lat=location_latlon["lat"], lon=location_latlon["lon"]),
                    radius=radius_miles * MILES_TO_METERS,
                ),
            )
        )

    query_filter = Filter(must=must or None, must_not=must_not or None)

    results = _client.query_points(
        collection_name=settings.qdrant_collection_name,
        query=vector,
        query_filter=query_filter,
        limit=6,
    ).points

    if not results:
        return None
    
    # L3 Norm
    scores = [result.score ** 3 for result in results]
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
