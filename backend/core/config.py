"""Environment-backed deployment configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///job_agent.db")
    api_token: str = os.getenv("TALENTALIGN_API_TOKEN", "")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))
    queue_poll_seconds: int = int(os.getenv("QUEUE_POLL_SECONDS", "5"))
    ingestion_config: str = os.getenv("INGESTION_CONFIG", "deploy/ingestion.yml")


settings = Settings()
