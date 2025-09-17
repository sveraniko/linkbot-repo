from pydantic_settings import BaseSettings
from pydantic import Field
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# LLM settings
LLM_DISABLED = os.getenv("LLM_DISABLED", "0") == "1"  # 0 = ON by default, 1 = OFF

class Settings(BaseSettings):
    bot_token: str = Field(default=os.getenv("BOT_TOKEN", ""), alias="BOT_TOKEN")
    openai_api_key: str = Field(default=os.getenv("OPENAI_API_KEY", ""), alias="OPENAI_API_KEY")
    database_url: str = Field(default=os.getenv("DATABASE_URL", "postgresql+asyncpg://memuser:secret@db:5432/memdb"), alias="DATABASE_URL")

    project_max_chunks: int = Field(default=200, alias="PROJECT_MAX_CHUNKS")
    chunk_size: int = Field(default=1600, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=150, alias="CHUNK_OVERLAP")
    
    # MinIO settings
    minio_endpoint: str | None = Field(default=None, alias="MINIO_ENDPOINT")
    minio_access_key: str | None = Field(default=None, alias="MINIO_ACCESS_KEY")
    minio_secret_key: str | None = Field(default=None, alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="memory", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    
    @property
    def DATABASE_URL(self) -> str:
        """Uppercase property for Alembic compatibility."""
        return self.database_url

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()