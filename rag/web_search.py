"""Web search augmentation for RAG pipeline.

Two search providers:
1. DuckDuckGo — always runs silently in background (free, no API key, open source)
2. Google Custom Search — user-controlled via Settings toggle

Both are called in parallel. Results are merged into a single context
block that gets appended to the LLM prompt alongside local RAG sources.
If no internet or API fails — graceful fallback to local-only.
"""
import logging
import re
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.config import settings

log = logging.getLogger("lexardor.web_search")

# Legal sites to prioritize in searches
SERBIAN_LEGAL_SITES = [
    "paragraf.rs", "propisi.net", "pravniforum.rs",
    "vfranja.com", "sudovi.rs", "minrzs.gov.rs",
]


def _build_legal_query(query: str) -> str:
    """Enhance query with legal context for web search."""
    words = query.split()
    if len(words) > 20:
        query = " ".join(words[:20])
    return f"srpsko pravo {query}"


def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """Search via DuckDuckGo (free, no API key needed). Returns list of {title, snippet, url}."""
    try:
        from ddgs import DDGS

        search_query = _build_legal_query(query)
        results = []

        with DDGS() as ddgs:
            for r in ddgs.text(search_query, region="rs-sr", max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                    "source": "duckduckgo",
                })

        log.info("DuckDuckGo returned %d results for: %s", len(results), query[:60])
        return results

    except ImportError:
        log.warning("ddgs not installed (pip install ddgs)")
        return []
    except Exception as e:
        log.warning("DuckDuckGo search failed: %s", e)
        return []


def _google_search(query: str, api_key: str, cx: str, max_results: int = 5) -> list[dict]:
    """Search via Google Custom Search API. Returns list of {title, snippet, url}.

    Args:
        api_key: User's Google API key (from Settings)
        cx: Google Custom Search Engine ID (from Settings)
    """
    if not api_key or not cx:
        return []

    search_query = _build_legal_query(query)

    try:
        r = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cx,
                "q": search_query,
                "num": min(max_results, 10),
                "lr": "lang_sr",
                "gl": "rs",
            },
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()

        results = []
        for item in data.get("items", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
                "source": "google",
            })
        log.info("Google search returned %d results for: %s", len(results), query[:60])
        return results

    except httpx.TimeoutException:
        log.warning("Google Custom Search timeout")
        return []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            log.warning("Google Custom Search daily limit reached")
        else:
            log.warning("Google Custom Search failed: %s", e)
        return []
    except Exception as e:
        log.warning("Google Custom Search failed: %s", e)
        return []


def search_web(query: str, google_api_key: str | None = None,
               google_cx: str | None = None,
               google_enabled: bool = False) -> list[dict]:
    """Run web searches in parallel. Always runs DuckDuckGo; Google only if enabled.

    Returns merged, deduplicated results sorted by relevance.
    """
    results = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}

        # DuckDuckGo always runs (hidden from user, free, no API key)
        futures[executor.submit(_ddg_search, query)] = "duckduckgo"

        # Google only if user enabled it and provided credentials
        if google_enabled and google_api_key and google_cx:
            futures[executor.submit(_google_search, query, google_api_key, google_cx)] = "google"

        for future in as_completed(futures, timeout=12):
            try:
                provider_results = future.result()
                results.extend(provider_results)
            except Exception as e:
                provider = futures[future]
                log.warning("Web search provider %s failed: %s", provider, e)

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for r in results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(r)

    # Prioritize Google results (user chose to enable them), then DuckDuckGo
    unique.sort(key=lambda x: (0 if x["source"] == "google" else 1))

    return unique[:8]  # Max 8 web results


def format_web_context(web_results: list[dict]) -> str:
    """Format web search results into context string for LLM prompt."""
    if not web_results:
        return ""

    parts = []
    for i, r in enumerate(web_results, 1):
        snippet = r["snippet"][:300] if r["snippet"] else ""
        # Clean HTML tags from snippet
        snippet = re.sub(r'<[^>]+>', '', snippet)
        parts.append(
            f"[Online izvor {i}] {r['title']}\n"
            f"Izvor: {r['url']}\n"
            f"{snippet}\n"
        )

    return "\nDODATNI ONLINE IZVORI (koristi za proveru i dopunu):\n" + "\n".join(parts)
