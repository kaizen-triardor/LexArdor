"""LLM client using llama.cpp server (OpenAI-compatible API)."""
import json
import httpx
from core.config import settings


class OllamaClient:
    """Despite the name (kept for compatibility), this talks to llama-server's
    OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, model: str = None, base_url: str = None):
        self.model = model or settings.ollama_model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    def is_available(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url}/v1/models", timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            return [self.model]

    def generate(self, prompt: str, system: str = None,
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        r = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def generate_stream(self, prompt: str, system: str = None,
                        temperature: float = 0.3, max_tokens: int = 2048):
        """Yield content tokens as they stream from llama-server."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        with httpx.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json={
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
            timeout=180,
        ) as r:
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
