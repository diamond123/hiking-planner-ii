from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, GeoPoint, GeoRadius, MatchAny

from app.config import settings

_client = QdrantClient(path=settings.qdrant_full_path)
_embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)

MILES_TO_METERS = 1609.34
GEO_RADIUS_MILES = 50


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
        must.append(
            FieldCondition(
                key="metadata.location",
                geo_radius=GeoRadius(
                    center=GeoPoint(lat=location_latlon["lat"], lon=location_latlon["lon"]),
                    radius=GEO_RADIUS_MILES * MILES_TO_METERS,
                ),
            )
        )

    query_filter = Filter(must=must or None, must_not=must_not or None)

    results = _client.query_points(
        collection_name=settings.qdrant_collection_name,
        query=vector,
        query_filter=query_filter,
        limit=1,
    ).points

    if not results:
        return None
    return results[0].payload
