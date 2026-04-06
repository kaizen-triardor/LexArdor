"""LLM client using llama.cpp server (OpenAI-compatible API)."""
import json
import logging
import re
import httpx
from core.config import settings

log = logging.getLogger("lexardor.ollama")


def _clean_response(text: str) -> str:
    """Strip thinking tokens, reasoning preamble, and English chain-of-thought from LLM output.

    Handles:
    - <think>...</think> blocks (Qwen/DeepSeek thinking mode)
    - RAZMIŠLJANJE: ... ODGOVOR: sections
    - "Let me analyze..." English preamble before Serbian answer
    """
    if not text:
        return text

    # Strip <think>...</think> blocks (including multiline)
    text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)

    # Strip RAZMIŠLJANJE: section, keep ODGOVOR: content
    if "ODGOVOR:" in text:
        text = text.split("ODGOVOR:", 1)[1].strip()
    elif "RAZMIŠLJANJE:" in text:
        parts = text.split("\n\n", 1)
        text = parts[-1].strip()

    # Strip English reasoning preamble (e.g., "Let me analyze this question carefully.\n\n...")
    # Only strip if there's Serbian content after it
    english_preamble = re.match(
        r'^((?:Let me|I (?:need to|will|should|can|must|cannot|don\'t)|The user|Looking at|None of|This requires|'
        r'I must follow|Based on|However|Unfortunately|First|Now|Here|Note:).*?\n\n)+',
        text, flags=re.DOTALL | re.IGNORECASE
    )
    if english_preamble:
        after = text[english_preamble.end():]
        # Only strip if remaining text has Cyrillic or common Serbian Latin chars
        if after and (re.search(r'[а-яА-ЯčćžšđČĆŽŠĐ]', after) or re.search(r'(?:Član|zakon|propis|odgovor)', after, re.IGNORECASE)):
            text = after.strip()

    return text.strip()


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
        except Exception as e:
            log.debug("LLM server health check failed: %s", e)
            return False

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url}/v1/models", timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.warning("Failed to list models from LLM server: %s", e)
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
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content", "")
        # Qwen 3.5 thinking mode: actual answer may be in content after reasoning
        # If content is empty, check reasoning_content
        if not content.strip() and msg.get("reasoning_content"):
            content = msg["reasoning_content"]
        return _clean_response(content)

    def generate_stream(self, prompt: str, system: str = None,
                        temperature: float = 0.3, max_tokens: int = 2048):
        """Yield content tokens as they stream from llama-server.

        Buffers and strips <think>...</think> blocks so they never reach the user.
        Also strips English reasoning preamble before Serbian content.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        buffer = []
        in_think = False
        think_done = False
        preamble_check_done = False

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
                    content = delta.get("content", "") or delta.get("reasoning_content", "")
                    if not content:
                        continue

                    # Buffer everything until <think> block is closed
                    if not think_done:
                        buffer.append(content)
                        joined = "".join(buffer)
                        if "<think>" in joined and "</think>" not in joined:
                            in_think = True
                            continue
                        if in_think and "</think>" in joined:
                            # Strip the think block and emit remaining
                            cleaned = re.sub(r'<think>.*?</think>\s*', '', joined, flags=re.DOTALL)
                            think_done = True
                            buffer = []
                            if cleaned.strip():
                                yield cleaned
                            continue
                        # No think block after reasonable buffer — flush
                        if len(joined) > 200 and "<think>" not in joined:
                            think_done = True
                            # Check for English preamble in buffer
                            cleaned = _clean_response(joined)
                            buffer = []
                            if cleaned.strip():
                                yield cleaned
                            continue
                        if not in_think:
                            continue  # Keep buffering
                        continue
                    else:
                        # After think block handled, stream directly
                        if not preamble_check_done:
                            # First chunk after think — check for leftover preamble
                            preamble_check_done = True
                            content = content.lstrip()
                        if content:
                            yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

        # Flush any remaining buffer (e.g., short responses with no think block)
        if buffer:
            joined = "".join(buffer)
            cleaned = _clean_response(joined)
            if cleaned.strip():
                yield cleaned
