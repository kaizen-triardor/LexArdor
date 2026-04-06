"""Model Router — manages multiple local LLM models for different pipeline stages.

LexArdor uses a multi-model architecture:
1. Fast model (Qwen 9B Q8, Gemma 4 E4B) — query classification, simple questions
2. Reasoning model (DeepSeek-R1 32B, Qwen 27B, Gemma 4 31B) — legal analysis, chain-of-thought
3. Verifier model (Saul-7B, Gemma 12B) — citation check, consistency verification
4. Agent model (Gemma 4 E2B) — AI agent, runs concurrently on separate port

The main pipeline runs one model at a time via llama-server (port 8081).
The agent model can run concurrently on a separate port for parallel processing.
The router handles model switching by restarting llama-server with the correct model.
"""
from __future__ import annotations
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

from core.config import settings

log = logging.getLogger("lexardor.model_router")

# ── llama-server binary ─────────────────────────────────────────────────────

LLAMA_SERVER = os.environ.get(
    "LLAMA_SERVER", str(Path.home() / ".local/bin/llama-server-cuda")
)
LLAMA_PORT = 8081

# ── Model Registry ───────────────────────────────────────────────────────────

MODELS = {
    "qwen9b": {
        "name": "Qwen 3.5 9B Q8",
        "path": settings.model_fast,
        "role": "fast",
        "ctx_size": 16384,
        "description": "Brzi model za klasifikaciju i jednostavna pitanja",
    },
    "qwen9b_opus": {
        "name": "Qwen 3.5 9B Opus Distilled Q8",
        "path": settings.model_fast_opus,
        "role": "fast",
        "ctx_size": 16384,
        "description": "Claude Opus reasoning u malom formatu — brzi + pametni",
    },
    "lexardor_opus": {
        "name": "LexArdor Opus 9B Legal Q8",
        "path": settings.model_lexardor_opus,
        "role": "fast",
        "ctx_size": 16384,
        "description": "Fine-tuned za srpsko pravo — Opus reasoning + pravni trening",
    },
    "qwen27b": {
        "name": "Qwen 3.5 27B Opus Distilled Q4",
        "path": settings.model_reasoning_qwen27b,
        "role": "reasoning",
        "ctx_size": 16384,
        "description": "Claude Opus reasoning patterns — strukturirani odgovori",
    },
    "deepseek": {
        "name": "DeepSeek-R1-Distill-Qwen-32B Q4",
        "path": settings.model_reasoning_deepseek,
        "role": "reasoning",
        "ctx_size": 16384,
        "description": "Najjači reasoning model — chain-of-thought pravna analiza",
    },
    "gemma4_31b": {
        "name": "Gemma 4 31B Q4",
        "path": settings.model_gemma4_31b,
        "role": "reasoning",
        "ctx_size": 16384,
        "description": "Gemma 4 veliki — napredna pravna analiza i reasoning",
    },
    "gemma4_4b": {
        "name": "Gemma 4 E4B Q8",
        "path": settings.model_gemma4_4b,
        "role": "verifier",
        "ctx_size": 16384,
        "description": "Gemma 4 srednji — verifikacija citata i dokumenata",
    },
    "gemma4_2b": {
        "name": "Gemma 4 E2B Q8",
        "path": settings.model_gemma4_2b,
        "role": "verifier",
        "ctx_size": 8192,
        "description": "Gemma 4 najmanji — brza verifikacija konzistentnosti",
    },
}


# ── Runtime state ────────────────────────────────────────────────────────────

_current_model_key: str | None = None
_llama_process: subprocess.Popen | None = None
_llama_log_file = None  # Track log file handle to prevent leaks


def get_current_model_key() -> str | None:
    """Return the key of the model currently loaded in llama-server."""
    return _current_model_key


def get_current_model() -> dict | None:
    """Return full info about the currently loaded model."""
    if _current_model_key and _current_model_key in MODELS:
        return {"key": _current_model_key, **MODELS[_current_model_key]}
    return None


def _kill_llama_server():
    """Kill any running llama-server on LLAMA_PORT."""
    global _llama_process
    # Kill our tracked process
    if _llama_process and _llama_process.poll() is None:
        _llama_process.terminate()
        try:
            _llama_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _llama_process.kill()
        _llama_process = None
    # Also kill any orphan on the port
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{LLAMA_PORT}", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split():
            try:
                os.kill(int(pid_str), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
    except Exception as e:
        log.warning("Failed to kill orphan llama-server processes: %s", e)
    time.sleep(1)


def _wait_for_health(timeout: int = 120) -> bool:
    """Wait for llama-server /health to return ok."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://localhost:{LLAMA_PORT}/health", timeout=3)
            if r.status_code == 200:
                return True
        except Exception as e:
            log.debug("Health check not ready yet: %s", e)
        time.sleep(2)
    return False


def swap_model(key: str) -> dict:
    """Stop current llama-server and start with a different model.

    Returns {"ok": True, "model": key, "name": ...} on success,
    or {"ok": False, "error": "..."} on failure.
    """
    global _current_model_key, _llama_process

    if key not in MODELS:
        return {"ok": False, "error": f"Unknown model key: {key}"}

    model_info = MODELS[key]
    model_path = model_info["path"]

    if not Path(model_path).exists():
        return {"ok": False, "error": f"Model file not found: {model_path}"}

    # Already loaded?
    if key == _current_model_key:
        try:
            r = httpx.get(f"http://localhost:{LLAMA_PORT}/health", timeout=3)
            if r.status_code == 200:
                return {"ok": True, "model": key, "name": model_info["name"],
                        "message": "Model already loaded"}
        except Exception as e:
            log.warning("Model health check failed, will restart: %s", e)

    log.info("Swapping model → %s (%s)", key, model_info["name"])
    _kill_llama_server()

    # LD_LIBRARY_PATH for CUDA
    env = os.environ.copy()
    cuda_lib = str(Path.home() / ".local/lib/llama")
    env["LD_LIBRARY_PATH"] = f"{cuda_lib}:{env.get('LD_LIBRARY_PATH', '')}"

    ctx_size = model_info["ctx_size"]
    threads = max(1, os.cpu_count() // 2) if os.cpu_count() else 4

    cmd = [
        LLAMA_SERVER,
        "--model", model_path,
        "--n-gpu-layers", "99",
        "--port", str(LLAMA_PORT),
        "--host", "0.0.0.0",
        "--ctx-size", str(ctx_size),
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--flash-attn", "on",
        "--threads", str(threads),
    ]

    log.info("Starting: %s", " ".join(cmd[:6]) + " ...")
    global _llama_log_file
    if _llama_log_file:
        _llama_log_file.close()
    _llama_log_file = open("/tmp/lexardor-llama.log", "w")
    _llama_process = subprocess.Popen(
        cmd, env=env,
        stdout=_llama_log_file,
        stderr=subprocess.STDOUT,
    )

    if _wait_for_health(timeout=120):
        _current_model_key = key
        log.info("Model %s loaded successfully", key)
        return {"ok": True, "model": key, "name": model_info["name"]}
    else:
        _current_model_key = None
        return {"ok": False, "error": f"llama-server failed to start with {key} (timeout)"}


def detect_loaded_model() -> str | None:
    """Try to detect which model is currently loaded by checking health."""
    global _current_model_key
    try:
        r = httpx.get(f"http://localhost:{LLAMA_PORT}/health", timeout=3)
        if r.status_code != 200:
            _current_model_key = None
            return None
    except Exception as e:
        log.warning("Failed to detect loaded model: %s", e)
        _current_model_key = None
        return None

    # Server is running — check env hint from start.sh, otherwise assume fast
    if _current_model_key is None:
        hint = os.environ.get("LEXARDOR_INITIAL_MODEL", "qwen9b")
        _current_model_key = hint if hint in MODELS else "qwen9b"
        log.info("Detected running model: %s", _current_model_key)
    return _current_model_key


# ── Query helpers (unchanged API) ────────────────────────────────────────────

def get_available_models() -> dict:
    """Return models that are actually downloaded (file exists)."""
    current = get_current_model_key()
    available = {}
    for key, info in MODELS.items():
        info_copy = dict(info)
        exists = Path(info["path"]).exists()
        info_copy["available"] = exists
        info_copy["size_gb"] = round(Path(info["path"]).stat().st_size / (1024**3), 1) if exists else 0
        info_copy["loaded"] = (key == current)
        available[key] = info_copy
    return available


def get_active_reasoning_model() -> dict:
    """Get the currently configured reasoning model."""
    key = settings.active_reasoning_model
    if key in MODELS and Path(MODELS[key]["path"]).exists():
        return {"key": key, **MODELS[key]}
    # Fallback chain
    for fallback in ["qwen27b", "deepseek", "gemma4_31b"]:
        if fallback in MODELS and Path(MODELS[fallback]["path"]).exists():
            return {"key": fallback, **MODELS[fallback]}
    return get_model_for_role("fast")


def get_active_verifier_model() -> dict:
    """Get the currently configured verifier model."""
    key = settings.active_verifier_model
    if key in MODELS and Path(MODELS[key]["path"]).exists():
        return {"key": key, **MODELS[key]}
    if "gemma4_4b" in MODELS and Path(MODELS["gemma4_4b"]["path"]).exists():
        return {"key": "gemma4_4b", **MODELS["gemma4_4b"]}
    if "gemma4_2b" in MODELS and Path(MODELS["gemma4_2b"]["path"]).exists():
        return {"key": "gemma4_2b", **MODELS["gemma4_2b"]}
    return {"key": "fast", **MODELS["fast"]}


def get_model_for_role(role: str) -> dict:
    """Get the best available model for a given role."""
    if role == "fast":
        if "qwen9b" in MODELS and Path(MODELS["qwen9b"]["path"]).exists():
            return {"key": "qwen9b", **MODELS["qwen9b"]}
        for key, info in MODELS.items():
            if info["role"] == "fast" and Path(info["path"]).exists():
                return {"key": key, **info}
    elif role == "reasoning":
        return get_active_reasoning_model()
    elif role == "verifier":
        return get_active_verifier_model()
    return {"key": "fast", **MODELS["fast"]}
