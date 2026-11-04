from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: str = "development"
    frontend_origin: str = "http://localhost:5173"

    # PostgreSQL
    database_url: SecretStr = Field(min_length=1)
    migration_database_url: SecretStr = Field(min_length=1)

    # Supabase Auth and Storage
    supabase_url: HttpUrl
    supabase_secret_key: SecretStr = Field(min_length=1)
    supabase_storage_bucket: str = "receipts"
    supabase_jwt_issuer: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_url: str = ""

    # OpenAI
    openai_api_key: SecretStr = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    vision_model: str = Field(min_length=1)
    transcription_model: str = Field(min_length=1)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)

    # Pinecone
    pinecone_api_key: SecretStr = Field(min_length=1)
    pinecone_index_name: str = "bonwise-dev"
    pinecone_index_host: str = ""

    # LangSmith
    langsmith_api_key: SecretStr = Field(min_length=1)
    langsmith_project: str = "bonwise-dev"
    langsmith_endpoint: HttpUrl = "https://eu.api.smith.langchain.com"
    langsmith_tracing: bool = True
    langsmith_hide_inputs: bool = True
    langsmith_hide_outputs: bool = True

    @model_validator(mode="after")
    def derive_auth_urls(self):
        base = str(self.supabase_url).rstrip("/")

        if not self.supabase_jwt_issuer:
            self.supabase_jwt_issuer = f"{base}/auth/v1"

        if not self.supabase_jwks_url:
            self.supabase_jwks_url = (
                f"{base}/auth/v1/.well-known/jwks.json"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()