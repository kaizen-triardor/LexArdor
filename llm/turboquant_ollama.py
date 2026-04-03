"""
LexArdor KV Cache Optimization Info

The real KV cache quantization happens at the llama-server level via native flags:
  --cache-type-k q4_0   (4-bit key cache — attention scores stay accurate)
  --cache-type-v q8_0   (8-bit value cache — weighted sums need more precision)

This is the production equivalent of Google's TurboQuant research paper.
llama.cpp implements it in optimized C/C++ with CUDA support, which is far
more practical than TurboQuant's Python/PyTorch research implementation.

Memory savings (Qwen3.5-9B, 32K context):
  FP16 KV cache: ~2 GB
  q4_0/q8_0:    ~750 MB  (2.7x savings)
  q4_0/q4_0:    ~500 MB  (4x savings, slightly lower quality)

This file kept for reference. The actual LLM client is in ollama.py.
"""

import httpx
from typing import Dict, Any


def get_server_info(base_url: str = "http://localhost:8081") -> Dict[str, Any]:
    """Query llama-server for its current configuration including KV cache settings."""
    try:
        # /props endpoint returns server configuration
        r = httpx.get(f"{base_url}/props", timeout=5)
        r.raise_for_status()
        props = r.json()

        # /health gives slot info
        h = httpx.get(f"{base_url}/health", timeout=5)
        health = h.json() if h.status_code == 200 else {}

        return {
            "status": "ok",
            "model": props.get("default_generation_settings", {}).get("model", "unknown"),
            "ctx_size": props.get("default_generation_settings", {}).get("n_ctx", 0),
            "server_props": props,
            "health": health,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
