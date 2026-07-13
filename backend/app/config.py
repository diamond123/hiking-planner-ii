from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Populate os.environ too, since some libraries (langsmith tracing, the Tavily
# API wrapper) read env vars directly rather than going through our Settings object.
load_dotenv(BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    openai_api_key: str
    tavily_api_key: str

    qdrant_path: str = "qdrant_data"
    qdrant_collection_name: str = "hiking_docs"

    backend_api_key: str

    langsmith_tracing: bool = False
    langsmith_project: str = "HikingPlannerII"
    langsmith_api_key: str | None = None

    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    @property
    def qdrant_full_path(self) -> str:
        p = Path(self.qdrant_path)
        return str(p if p.is_absolute() else BACKEND_DIR / p)

    @property
    def documents_db_path(self) -> str:
        return str(Path(self.qdrant_full_path) / "documents.db")


settings = Settings()
