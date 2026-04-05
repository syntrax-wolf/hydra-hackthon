from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Config:
    openrouter_api_key: str
    openrouter_model: str
    openrouter_coding_model: str
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    server_host: str
    server_port: int
    sandbox_timeout: int
    generated_dir: str
    youtube_api_key: str
    resume_upload_dir: str


def load_config() -> Config:
    return Config(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "qwen/qwen3.5-27b"),
        openrouter_coding_model=os.getenv("OPENROUTER_CODING_MODEL", os.getenv("OPENROUTER_MODEL", "qwen/qwen3.5-27b")),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_user=os.getenv("POSTGRES_USER", "postgres"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
        postgres_db=os.getenv("POSTGRES_DB", "horizon"),
        server_host=os.getenv("SERVER_HOST", "0.0.0.0"),
        server_port=int(os.getenv("SERVER_PORT", "8501")),
        sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT", "60")),
        generated_dir=os.getenv("GENERATED_DIR", "./generated"),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
        resume_upload_dir=os.getenv("RESUME_UPLOAD_DIR", "./resumes"),
    )


config = load_config()
