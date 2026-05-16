from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "cng"
    POSTGRES_PASSWORD: str = "cngpass"
    POSTGRES_DB: str = "clinical_graph"

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4jpass"

    # Vault
    VAULT_PATH: str = "/data/vault"

    # AI: any OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, vLLM, …).
    # `mock` runs a deterministic offline extractor.
    AI_PROVIDER: str = "mock"  # mock | openai | custom
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    AI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Coding standards toggles
    CODING_ICD10: bool = True
    CODING_SNOMEDCT: bool = True
    CODING_LOINC: bool = True
    CODING_RXNORM: bool = True

    LOG_LEVEL: str = "INFO"

    # Deployment / security
    CORS_ORIGINS: str = "*"  # comma-separated; "*" means "any" (dev only)
    API_KEY: str = ""  # if non-empty, X-API-Key is required on /api/emr|/api/config|/api/export|/api/facts

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def vault_dir(self) -> Path:
        p = Path(self.VAULT_PATH)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
