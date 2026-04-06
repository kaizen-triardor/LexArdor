"""LexArdor configuration via pydantic-settings."""
import logging
from pydantic_settings import BaseSettings
from pathlib import Path

logger = logging.getLogger(__name__)

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
    model_fast_opus: str = str(Path.home() / "models/lexardor/Qwen3.5-9B-Claude-Opus-Reasoning-Distilled.Q8_0.gguf")
    model_lexardor_opus: str = str(Path.home() / "models/lexardor/LexArdor-Opus-9B-Legal-Q8_0.gguf")
    model_reasoning_deepseek: str = str(Path.home() / "models/lexardor/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf")
    model_reasoning_qwen27b: str = str(Path.home() / "models/lexardor/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled.i1-Q4_K_M.gguf")
    # Removed: model_verifier_saul, model_verifier_gemma, model_fast_q4
    model_gemma4_2b: str = str(Path.home() / "models/lexardor/gemma-4-e2b-it-Q8_0.gguf")
    model_gemma4_4b: str = str(Path.home() / "models/lexardor/gemma-4-E4B-it-Q8_0.gguf")
    model_gemma4_31b: str = str(Path.home() / "models/lexardor/gemma-4-31B-it-Q4_K_M.gguf")

    # Active model selection (changeable via settings)
    active_reasoning_model: str = "deepseek"  # deepseek | qwen27b | gemma4_31b
    active_verifier_model: str = "gemma4_4b"  # gemma4_4b | gemma4_2b
    agent_model: str = "gemma4_2b"             # model for AI agent (runs concurrently on separate port)

    # Hardware tier (auto-detected or manual)
    hardware_tier: str = "auto"  # auto | high | mid | low

    # ── Web Search (background augmentation) ────────────────────
    # DuckDuckGo runs automatically in background (free, no API key needed)
    google_api_key: str = ""  # Google Custom Search API key (user-controlled via Settings)
    google_cx: str = ""  # Google Custom Search Engine ID

    class Config:
        env_file = str(BASE_DIR / ".env")


settings = Settings()


# ── Auto-generate secret_key on first boot ──────────────────────────────────

def _ensure_unique_secret():
    """Generate a unique secret_key if still using the default placeholder."""
    if settings.secret_key in ("lexardor-local-2026-change-me", "lexardor-stoic-fire-2026-change-me"):
        import secrets
        new_key = f"lexardor-{secrets.token_hex(24)}"
        settings.secret_key = new_key
        # Persist to .env
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            content = env_path.read_text()
            import re
            content = re.sub(r'SECRET_KEY=.*', f'SECRET_KEY={new_key}', content)
            env_path.write_text(content)
            logger.info("Generated unique SECRET_KEY for this installation")

_ensure_unique_secret()


# ── Hardware tier detection ──────────────────────────────────────────────────

HARDWARE_TIERS = {
    "high": {
        "label": "High-End (24+ GB VRAM)",
        "min_vram_gb": 16,
        "fast": "fast",           # Qwen 9B Q8 (8.9 GB)
        "reasoning": "deepseek",  # DeepSeek 32B Q4 (18.5 GB)
        "verifier": "gemma4_4b",  # Gemma 4 E4B Q8 (7.6 GB)
    },
    "mid": {
        "label": "Mid-Range (8 GB VRAM)",
        "min_vram_gb": 6,
        "fast": "gemma4_4b",      # Gemma 4 E4B Q8 (7.6 GB)
        "reasoning": "gemma4_4b", # Same
        "verifier": "gemma4_2b",  # Gemma 4 E2B Q8 (4.6 GB)
    },
    "low": {
        "label": "Low-End (CPU / 4 GB VRAM)",
        "min_vram_gb": 0,
        "fast": "gemma4_2b",      # Gemma 4 E2B Q8 (4.6 GB)
        "reasoning": "gemma4_2b", # Same (only model that fits)
        "verifier": "gemma4_2b",  # Same
    },
}


def detect_gpu_vram() -> int:
    """Detect GPU VRAM in GB. Returns 0 if no GPU found."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            vram_mb = int(result.stdout.strip().split("\n")[0])
            return vram_mb // 1024
    except Exception as e:
        logger.warning("GPU VRAM detection failed: %s", e)
    return 0


def detect_hardware_tier() -> str:
    """Auto-detect hardware tier based on GPU VRAM."""
    if settings.hardware_tier != "auto":
        return settings.hardware_tier
    vram = detect_gpu_vram()
    if vram >= 16:
        return "high"
    elif vram >= 6:
        return "mid"
    else:
        return "low"
