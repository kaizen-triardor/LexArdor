"""Unified LLM provider abstraction for model routing.

Supports local (llama-server) and external (OpenAI/Anthropic/Google/xAI)
providers through a single interface. Enables BYO (Bring Your Own) API
key mode for customers.
"""
from __future__ import annotations
from typing import Iterator
from llm.ollama import OllamaClient
from llm.external import query_external, anonymize_prompt, deanonymize_response
from core.config import settings


class LLMProvider:
    """Base interface for LLM providers."""

    def generate(self, prompt: str, system: str = None,
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
        raise NotImplementedError

    def generate_stream(self, prompt: str, system: str = None,
                        temperature: float = 0.3, max_tokens: int = 2048) -> Iterator[str]:
        raise NotImplementedError

    def is_available(self) -> bool:
        return False


class LocalProvider(LLMProvider):
    """Wraps OllamaClient (llama-server) for local inference."""

    def __init__(self, model: str = None, heavy: bool = False):
        if model:
            self._model = model
        elif heavy:
            self._model = settings.ollama_model_heavy
        else:
            self._model = settings.ollama_model
        self._client = OllamaClient(model=self._model)

    def generate(self, prompt: str, system: str = None,
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
        return self._client.generate(prompt, system=system,
                                      temperature=temperature, max_tokens=max_tokens)

    def generate_stream(self, prompt: str, system: str = None,
                        temperature: float = 0.3, max_tokens: int = 2048) -> Iterator[str]:
        return self._client.generate_stream(prompt, system=system,
                                             temperature=temperature, max_tokens=max_tokens)

    def is_available(self) -> bool:
        return self._client.is_available()


class ExternalProvider(LLMProvider):
    """Wraps external AI providers with optional anonymization."""

    def __init__(self, provider: str, api_key: str, model: str = None,
                 anonymize: bool = True, names_to_hide: list[str] = None):
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._anonymize = anonymize
        self._names_to_hide = names_to_hide or []

    def generate(self, prompt: str, system: str = None,
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
        # Anonymize if requested
        replacements = {}
        if self._anonymize:
            anon = anonymize_prompt(prompt, self._names_to_hide)
            prompt = anon.anonymized_text
            replacements = anon.replacements

        answer = query_external(self._provider, self._api_key, prompt,
                                model=self._model, system=system)

        # De-anonymize response
        if replacements:
            answer = deanonymize_response(answer, replacements)
        return answer

    def generate_stream(self, prompt: str, system: str = None,
                        temperature: float = 0.3, max_tokens: int = 2048) -> Iterator[str]:
        # External providers don't support streaming in current implementation
        # Fall back to full generation yielded as single token
        answer = self.generate(prompt, system=system, temperature=temperature,
                               max_tokens=max_tokens)
        yield answer

    def is_available(self) -> bool:
        return bool(self._api_key)


def get_provider(
    provider_name: str = "local",
    api_key: str = None,
    model: str = None,
    heavy: bool = False,
    anonymize: bool = True,
    names_to_hide: list[str] = None,
) -> LLMProvider:
    """Factory: get the right provider based on name.

    provider_name: "local", "openai", "anthropic", "google", "xai"
    """
    if provider_name == "local":
        return LocalProvider(model=model, heavy=heavy)
    return ExternalProvider(
        provider=provider_name,
        api_key=api_key or "",
        model=model,
        anonymize=anonymize,
        names_to_hide=names_to_hide,
    )
