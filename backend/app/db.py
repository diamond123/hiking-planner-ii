import sqlite3

from app.config import settings

_conn = sqlite3.connect(settings.documents_db_path, check_same_thread=False)


def get_document_by_source(source: str) -> str | None:
    cursor = _conn.execute("SELECT content FROM documents WHERE source = ?", (source,))
    row = cursor.fetchone()
    return row[0] if row else None


def get_sources_with_prefix(prefix: str) -> list[str]:
    """`source` values are URL-encoded scrape paths (e.g. "eastbayhikes%2Fpleasanton.html"),
    so a plain LIKE prefix match is safe - the prefix itself has no characters that need escaping.
    Used by search_qdrant's BAY_AREA_REGION_OVERRIDES filter (see geocode.py) to restrict results
    to the source site's own regional folder rather than trusting geo-radius distance alone.
    """
    cursor = _conn.execute("SELECT source FROM documents WHERE source LIKE ?", (f"{prefix}%",))
    return [row[0] for row in cursor.fetchall()]
