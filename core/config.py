"""LexArdor configuration via pydantic-settings."""
from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:8081"
    ollama_model: str = "qwen3.5-9b-opus"
    ollama_model_heavy: str = "qwen3.5-27b-opus"
    embedding_model: str = "intfloat/multilingual-e5-base"
    chroma_path: str = str(BASE_DIR / "data" / "chroma")
    laws_path: str = str(BASE_DIR / "data" / "laws")
    db_path: str = str(BASE_DIR / "data" / "lexardor.db")
    secret_key: str = "lexardor-local-2026-change-me"
    default_admin_user: str = "admin"
    default_admin_pass: str = "admin123"
    port: int = 8080
    installation_id: str = "LA-2026-0000"
    license_firm: str = ""
    app_version: str = "2.0.0"
    support_email: str = "triardor.studio@gmail.com"

    # ── SMTP (optional, for support report emails) ─────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""

    # ── Model Registry ──────────────────────────────────────────
    # Paths to GGUF model files
    model_fast: str = str(Path.home() / "models/lexardor/Qwen3.5-9B.Q8_0.gguf")
    model_reasoning_deepseek: str = str(Path.home() / "models/lexardor/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf")
    model_reasoning_qwen27b: str = str(Path.home() / "models/lexardor/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled.i1-Q4_K_M.gguf")
    model_verifier_saul: str = str(Path.home() / "models/lexardor/Saul-7B-Instruct-v1.i1-Q4_K_M.gguf")
    model_verifier_gemma: str = str(Path.home() / "models/lexardor/gemma-3-12b-it.Q4_K_M.gguf")

    # Active model selection (changeable via settings)
    active_reasoning_model: str = "deepseek"  # deepseek | qwen27b
    active_verifier_model: str = "gemma"       # gemma (saul unavailable as GGUF)

    class Config:
        env_file = str(BASE_DIR / ".env")


settings = Settings()
