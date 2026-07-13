import sqlite3

from app.config import settings

_conn = sqlite3.connect(settings.documents_db_path, check_same_thread=False)


def get_document_by_source(source: str) -> str | None:
    cursor = _conn.execute("SELECT content FROM documents WHERE source = ?", (source,))
    row = cursor.fetchone()
    return row[0] if row else None
