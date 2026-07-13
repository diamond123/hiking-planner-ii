"""One-off manual verification script: run with the server NOT running
(local Qdrant holds an exclusive lock, only one process may open it).

    uv run python scripts/verify_qdrant.py
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, GeoPoint, GeoRadius

QDRANT_PATH = os.environ["QDRANT_PATH"]
COLLECTION = os.environ["QDRANT_COLLECTION_NAME"]

EMBEDDING_MODELS = ["text-embedding-3-small", "text-embedding-ada-002"]
TEST_QUERY = "redwood forest hike with waterfall views"


def main():
    client = QdrantClient(path=QDRANT_PATH)
    info = client.get_collection(COLLECTION)
    print(f"Collection '{COLLECTION}': {info.points_count} points, "
          f"vector size {info.config.params.vectors.size}, "
          f"distance {info.config.params.vectors.distance}")

    for model in EMBEDDING_MODELS:
        print(f"\n=== embedding model: {model} ===")
        embeddings = OpenAIEmbeddings(model=model)
        vector = embeddings.embed_query(TEST_QUERY)
        print(f"vector dims: {len(vector)}")
        if len(vector) != info.config.params.vectors.size:
            print("  -> dimension mismatch, skipping search")
            continue
        results = client.query_points(collection_name=COLLECTION, query=vector, limit=5).points
        for r in results:
            md = r.payload.get("metadata", {})
            print(f"  score={r.score:.4f} title={md.get('title')!r} source={md.get('source')!r}")

    # geo_radius sanity check using a real query vector against Briones (37.927125, -122.155836)
    print("\n=== geo_radius filter check (50mi around Briones) ===")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    vector = embeddings.embed_query(TEST_QUERY)
    if len(vector) == info.config.params.vectors.size:
        geo_filter = Filter(
            must=[
                FieldCondition(
                    key="metadata.location",
                    geo_radius=GeoRadius(
                        center=GeoPoint(lat=37.927125, lon=-122.155836),
                        radius=50 * 1609.34,
                    ),
                )
            ]
        )
        results = client.query_points(
            collection_name=COLLECTION, query=vector, query_filter=geo_filter, limit=5
        ).points
        for r in results:
            md = r.payload.get("metadata", {})
            print(f"  score={r.score:.4f} title={md.get('title')!r} location={md.get('location')}")


if __name__ == "__main__":
    main()
