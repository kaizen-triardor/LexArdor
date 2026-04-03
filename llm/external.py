"""External AI client with prompt anonymization for safe cloud queries."""
import re
import httpx
from dataclasses import dataclass


@dataclass
class AnonymizationResult:
    """Result of anonymizing a prompt."""
    anonymized_text: str
    replacements: dict[str, str]  # placeholder -> original


# Serbian name patterns (common first/last names)
_SERBIAN_NAME_PATTERNS = [
    # JMBG (13 digits)
    (r'\b\d{13}\b', 'JMBG'),
    # Phone numbers
    (r'\b(?:\+381|0)\s*\d[\d\s\-]{7,12}\b', 'TELEFON'),
    # Email
    (r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b', 'EMAIL'),
    # Addresses (Ulica + number pattern)
    (r'(?:ul\.|ulica|bulevar|bul\.)\s+[A-ZČĆŠĐŽa-zčćšđž\s]+\s+\d+[a-z]?(?:/\d+)?', 'ADRESA'),
    # PIB (9 digits tax ID)
    (r'\bPIB[:\s]*\d{9}\b', 'PIB'),
    # MB (matični broj, 8 digits)
    (r'\b(?:MB|matični\s+broj)[:\s]*\d{8}\b', 'MATICNI_BROJ'),
    # Bank account (3-13-2 format)
    (r'\b\d{3}-\d{10,13}-\d{2}\b', 'RACUN'),
    # Dates of birth
    (r'\b\d{1,2}\.\d{1,2}\.\d{4}\.?\b', 'DATUM'),
]


def anonymize_prompt(text: str, extra_names: list[str] = None) -> AnonymizationResult:
    """Replace PII in text with placeholders.

    Returns the anonymized text and a mapping to restore originals.
    """
    replacements = {}
    result = text
    counter = {}

    # Apply regex patterns
    for pattern, label in _SERBIAN_NAME_PATTERNS:
        for match in re.finditer(pattern, result, re.IGNORECASE):
            original = match.group()
            if original in replacements.values():
                continue
            counter[label] = counter.get(label, 0) + 1
            placeholder = f"[{label}_{counter[label]}]"
            replacements[placeholder] = original
            result = result.replace(original, placeholder, 1)

    # Replace extra names provided by user
    if extra_names:
        for i, name in enumerate(extra_names, 1):
            name = name.strip()
            if name and name in result:
                placeholder = f"[LICE_{i}]"
                replacements[placeholder] = name
                result = result.replace(name, placeholder)

    return AnonymizationResult(anonymized_text=result, replacements=replacements)


def deanonymize_response(text: str, replacements: dict[str, str]) -> str:
    """Restore original values from placeholders in AI response."""
    result = text
    for placeholder, original in replacements.items():
        result = result.replace(placeholder, original)
    return result


# ── External AI providers ────────────────────────────────────────────────────

PROVIDERS = {
    "openai": {
        "name": "OpenAI (GPT)",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "default_model": "gpt-4o-mini",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250414"],
        "default_model": "claude-sonnet-4-20250514",
    },
    "google": {
        "name": "Google (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "default_model": "gemini-2.5-flash",
    },
    "xai": {
        "name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-3", "grok-3-mini"],
        "default_model": "grok-3-mini",
    },
    "perplexity": {
        "name": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "models": ["sonar-pro", "sonar", "sonar-deep-research"],
        "default_model": "sonar",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "mistral": {
        "name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
        "default_model": "mistral-small-latest",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it", "mixtral-8x7b-32768"],
        "default_model": "llama-3.3-70b-versatile",
    },
}


def query_external(provider: str, api_key: str, prompt: str,
                   model: str = None, system: str = None) -> str:
    """Send query to external AI provider using OpenAI-compatible API."""
    prov = PROVIDERS.get(provider)
    if not prov:
        raise ValueError(f"Unknown provider: {provider}")

    model = model or prov["default_model"]

    if provider == "anthropic":
        # Anthropic Messages API
        headers = {
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        # Truncate prompt if too long (Anthropic has input limits)
        if len(prompt) > 80000:
            prompt = prompt[:80000] + "\n\n[Tekst je skraćen zbog ograničenja API-ja]"
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        try:
            r = httpx.post(f"{prov['base_url']}/messages", json=body, headers=headers, timeout=120)
            r.raise_for_status()
            return r.json()["content"][0]["text"]
        except httpx.HTTPStatusError as e:
            # Parse Anthropic error for better message
            try:
                err_body = e.response.json()
                err_msg = err_body.get("error", {}).get("message", str(e))
            except Exception:
                err_msg = str(e)
            raise Exception(f"Anthropic API: {err_msg}")

    elif provider == "google":
        # Gemini API
        if len(prompt) > 80000:
            prompt = prompt[:80000] + "\n\n[Tekst je skraćen]"
        url = f"{prov['base_url']}/models/{model}:generateContent?key={api_key}"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            r = httpx.post(url, json=body, timeout=120)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except httpx.HTTPStatusError as e:
            try:
                err_msg = e.response.json().get("error", {}).get("message", str(e))
            except Exception:
                err_msg = str(e)
            raise Exception(f"Google API: {err_msg}")

    else:
        # OpenAI-compatible (OpenAI, xAI/Grok)
        if len(prompt) > 80000:
            prompt = prompt[:80000] + "\n\n[Tekst je skraćen]"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": messages, "max_tokens": 4096}
        try:
            r = httpx.post(f"{prov['base_url']}/chat/completions", json=body, headers=headers, timeout=120)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            try:
                err_msg = e.response.json().get("error", {}).get("message", str(e))
            except Exception:
                err_msg = str(e)
            raise Exception(f"{provider} API: {err_msg}")
