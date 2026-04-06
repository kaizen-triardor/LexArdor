# rag/pipeline.py
"""Full RAG pipeline: classify -> retrieve -> rerank -> reason -> verify."""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from rag.store import search, search_with_filters
from rag.reranker import rerank
from rag.graph import expand_with_cross_refs, fetch_articles_by_chroma_ids
from rag.reasoning import classify_query, get_system_prompt, verify_citations
from rag.web_search import search_web, format_web_context
from llm.ollama import OllamaClient
from llm.model_router import (
    get_current_model, get_model_for_role, get_active_reasoning_model,
    swap_model, detect_loaded_model, MODELS,
)
from core.transliterate import to_latin, detect_script
from core.config import settings

log = logging.getLogger("lexardor.pipeline")


SYSTEM_PROMPT_DETAILED = """Ti si LexArdor, AI pravni asistent za srpsko pravo.
UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). NE prikazuj razmišljanje ili thought process.

Odgovaraj na osnovu priloženih izvora. Citiraj članove inline (npr. "prema Članu 187 Zakona o radu").
Počni sa direktnim odgovorom u 1-2 rečenice, pa obrazloži.
NE ponavljaj informacije. NE pravi prazne sekcije. Budi precizan i koncizan.
Ako izvori ne pokrivaju pitanje — reci to kratko.
Maksimalno 1000 karaktera."""

SYSTEM_PROMPT_SHORT = """Ti si LexArdor. UVEK odgovaraj na SRPSKOM jeziku. NE prikazuj razmišljanje.
Odgovori u 2-3 rečenice sa ključnim članom zakona.
Ako nemaš informacije — reci to. Ne izmišljaj."""


# ── Law name extraction ─────────────────────────────────────────────────────

# Map gazette patterns to readable law names
_LAW_NAME_CACHE: dict[str, str] = {}


def _extract_law_name(gazette: str, slug: str = "") -> str:
    """Try to extract a human-readable law name from gazette text or slug."""
    if not gazette and not slug:
        return ""

    # Check cache
    cache_key = f"{gazette}|{slug}"
    if cache_key in _LAW_NAME_CACHE:
        return _LAW_NAME_CACHE[cache_key]

    # Try to derive from slug (most reliable)
    if slug:
        name = slug.replace("-", " ").replace("_", " ")
        # Capitalize first letter of each word for major words
        name = name.title()
        # Fix common patterns
        name = name.replace("Zakon O ", "Zakon o ")
        name = name.replace("Zakonik O ", "Zakonik o ")
        _LAW_NAME_CACHE[cache_key] = name
        return name

    # If gazette starts with ( or contains GLASNIK, it's a gazette ref not a name
    if gazette and not gazette.startswith("(") and "GLASNIK" not in gazette.upper():
        _LAW_NAME_CACHE[cache_key] = gazette
        return gazette

    _LAW_NAME_CACHE[cache_key] = ""
    return ""


# ── Confidence scoring ───────────────────────────────────────────────────────

def _calculate_confidence(query: str, hits: list[dict],
                          citations: dict | None = None,
                          answer_text: str | None = None) -> dict:
    """Enhanced confidence with level, reasons, and red flags.

    Returns {level: str, reasons: [str], red_flags: [str]} instead of plain string.
    Also returns legacy "level" key for backward compat.
    """
    reasons = []
    red_flags = []

    if not hits:
        return {"level": "low", "reasons": ["Nema pronađenih izvora"],
                "red_flags": ["Odgovor je bez pravnih izvora — zahteva ručnu proveru"]}

    top_score = hits[0]["score"]
    bm25_score = hits[0].get("bm25_score", 0)

    # Check keyword relevance
    from core.tokenizer import extract_query_keywords
    query_words = extract_query_keywords(query)
    keyword_ratio = 0.0
    if query_words:
        top_texts = " ".join(h["text"].lower() for h in hits[:3])
        keyword_hits = sum(1 for w in query_words if w in top_texts)
        keyword_ratio = keyword_hits / len(query_words)

    # Score-based assessment
    if top_score > 0.88 and keyword_ratio >= 0.5:
        level = "high"
        reasons.append("Visoka podudarnost vektora i ključnih reči")
    elif top_score > 0.82 and keyword_ratio >= 0.3:
        level = "medium"
        reasons.append("Umerena podudarnost izvora")
    elif keyword_ratio >= 0.5:
        level = "medium"
        reasons.append("Dobra podudarnost ključnih reči")
    else:
        level = "low"
        reasons.append("Niska podudarnost sa bazom propisa")

    if bm25_score > 10:
        reasons.append("Jak BM25 leksički pogodak")

    # Citation-based red flags
    if citations:
        if citations.get("flagged_count", 0) > 0:
            red_flags.append(f"{citations['flagged_count']} citat(a) nije potvrđeno u izvorima")
        if citations.get("flagged_count", 0) > citations.get("verified_count", 0):
            level = "low"
            red_flags.append("Većina citata nije potvrđena")

    # Answer text red flags
    if answer_text:
        hedging = ["nije sigurno", "moguće je", "ne mogu sa sigurnošću",
                    "nemam dovoljno", "nedovoljno informacija", "nisam siguran"]
        for phrase in hedging:
            if phrase in answer_text.lower():
                red_flags.append("AI je izrazio nesigurnost u odgovoru")
                if level == "high":
                    level = "medium"
                break

    if level == "low":
        red_flags.append("Ovaj odgovor zahteva proveru kvalifikovanog advokata")

    return {"level": level, "reasons": reasons, "red_flags": red_flags}


# ── Source formatting ────────────────────────────────────────────────────────

def _format_sources(hits: list[dict]) -> list[dict]:
    """Format hits into clean source objects with readable law names."""
    sources = []
    for h in hits:
        meta = h["metadata"]
        slug = meta.get("law_slug", "")
        gazette = meta.get("gazette", "")
        law_title = meta.get("law_title", "")

        # Get readable law name
        law_name = _extract_law_name(law_title, slug)
        if not law_name:
            law_name = _extract_law_name(gazette, slug)

        # Clean gazette for display
        clean_gazette = gazette
        if clean_gazette:
            clean_gazette = clean_gazette.strip("()\"\' ")
            if len(clean_gazette) > 80:
                clean_gazette = clean_gazette[:80] + "..."

        # Full text for "Prikaži ceo član" feature
        full_text = h["text"]

        # Temporal validity
        valid_from = meta.get("valid_from", "")
        valid_to = meta.get("valid_to", "")
        from datetime import date
        today = date.today().isoformat()
        if valid_from and valid_to:
            is_current = valid_from <= today <= valid_to
        elif valid_from and not valid_to:
            is_current = valid_from <= today
        else:
            is_current = None  # Unknown

        sources.append({
            "law": law_name,
            "law_raw": law_title,
            "article": meta.get("article_number", meta.get("chunk_index", "")),
            "gazette": clean_gazette,
            "text": full_text[:300] + "..." if len(full_text) > 300 else full_text,
            "full_text": full_text,
            "score": round(h.get("rerank_score", h["score"]), 3),
            "vec_score": h.get("vec_score", 0),
            "keyword_boost": h.get("keyword_boost", 0),
            "bm25_score": h.get("bm25_score", 0),
            "rerank_score": h.get("rerank_score"),
            "source_collection": h.get("source_collection", "core_laws"),
            "source_url": meta.get("source_url", ""),
            "slug": slug,
            "doc_type": meta.get("doc_type", ""),
            "authority_level": meta.get("authority_level", ""),
            "is_cross_ref": h.get("is_cross_ref", False),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "is_current": is_current,
        })
    return sources


# ── Build context ────────────────────────────────────────────────────────────

def build_context(hits: list[dict]) -> str:
    if not hits:
        return "Nema pronađenih relevantnih pravnih izvora."
    parts = []
    for i, hit in enumerate(hits, 1):
        meta = hit["metadata"]
        slug = meta.get("law_slug", "")
        law_name = _extract_law_name(meta.get("law_title", ""), slug)
        parts.append(
            f"[Izvor {i}] {law_name} — Član {meta.get('article_number', '?')} "
            f"({meta.get('gazette', '')})\n{hit['text']}\n"
        )
    return "\n".join(parts)


# ── Main query functions ─────────────────────────────────────────────────────

def _retrieve_and_rerank(search_query: str, top_k: int = 5,
                         reference_date: str | None = None,
                         doc_types: list[str] | None = None,
                         min_authority: int | None = None,
                         legal_domains: list[str] | None = None) -> tuple[list[dict], dict]:
    """Enhanced retrieval: search → expand cross-refs → rerank.

    Returns (reranked_hits, diagnostics).
    """
    # Step 1: Broad retrieval (40 candidates)
    candidates = search_with_filters(search_query, top_k=top_k, fetch_k=40,
                                     reference_date=reference_date,
                                     doc_types=doc_types,
                                     min_authority=min_authority,
                                     legal_domains=legal_domains)

    # Step 2: Expand with cross-referenced articles
    top_ids = [c["id"] for c in candidates[:10]]
    ref_ids = expand_with_cross_refs(top_ids, max_refs=5)
    if ref_ids:
        ref_articles = fetch_articles_by_chroma_ids(ref_ids)
        candidates.extend(ref_articles)

    # Step 3: Rerank with cross-encoder
    reranked = rerank(search_query, candidates, top_k=top_k)

    diagnostics = {
        "candidates_fetched": len(candidates),
        "cross_refs_added": len(ref_ids) if ref_ids else 0,
        "rerank_applied": True,
        "top_rerank_score": round(reranked[0].get("rerank_score", 0), 3) if reranked else 0,
    }

    return reranked, diagnostics


def query(user_query: str, top_k: int = 5, use_heavy_model: bool = False,
          short_answer: bool = False, chat_history: list[dict] = None,
          answer_mode: str = "balanced", reference_date: str | None = None,
          doc_types: list[str] | None = None,
          min_authority: int | None = None) -> dict:
    """Full pipeline: classify -> retrieve -> rerank -> reason -> verify."""
    search_query = to_latin(user_query) if detect_script(user_query) == "cyrillic" else user_query

    # Stage 1: Classify query
    query_class = classify_query(user_query)
    diagnostics_extra = {"query_class": query_class}
    if reference_date:
        diagnostics_extra["temporal_filter"] = reference_date
    if doc_types:
        diagnostics_extra["doc_type_filter"] = doc_types
    if min_authority:
        diagnostics_extra["authority_filter"] = min_authority

    # Stage 2+3: Retrieve and rerank + web search (in parallel)
    detected_domains = query_class.get("legal_domains", [])

    web_results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Local RAG retrieval
        rag_future = executor.submit(
            _retrieve_and_rerank, search_query, top_k, reference_date,
            doc_types, min_authority, detected_domains
        )
        # Web search (runs in parallel, hidden from user)
        # Google creds from server-side config (never in URL)
        g_key = settings.google_api_key
        g_cx = settings.google_cx
        g_enabled = bool(g_key and g_cx)
        web_future = executor.submit(
            search_web, search_query, g_key, g_cx, g_enabled
        )

        hits, diagnostics = rag_future.result()
        try:
            web_results = web_future.result()
        except Exception as e:
            log.warning("Web search failed (non-blocking): %s", e)

    diagnostics.update(diagnostics_extra)
    if web_results:
        diagnostics["web_sources"] = len(web_results)
        diagnostics["web_providers"] = list({r["source"] for r in web_results})

    context = build_context(hits)
    web_context = format_web_context(web_results)

    # Stage 4: Choose system prompt based on answer mode
    system = get_system_prompt(answer_mode, short_answer)

    # Build user prompt with optional chat history for follow-up context
    history_context = ""
    if chat_history:
        recent = chat_history[-6:]  # Last 3 exchanges
        history_parts = []
        for msg in recent:
            role = "Korisnik" if msg["role"] == "user" else "LexArdor"
            history_parts.append(f"{role}: {msg['content'][:200]}")
        history_context = "\nPRETHODNA KONVERZACIJA:\n" + "\n".join(history_parts) + "\n"

    user_prompt = f"""PRAVNI IZVORI:
{context}
{web_context}
{history_context}
PITANJE KORISNIKA:
{user_query}

Odgovori na osnovu priloženih pravnih izvora. Ako postoje online izvori, koristi ih za dopunu i proveru.
Navedi relevantne članove zakona."""

    # Stage 4b: Select model via router
    if use_heavy_model:
        target = get_active_reasoning_model()
    else:
        target = get_model_for_role("fast")

    current = get_current_model()
    model_key = target["key"]
    model_name = target["name"]

    # Auto-swap if requested model differs from loaded model
    if current is None or current["key"] != model_key:
        log.info("Auto-swap: %s → %s", current["key"] if current else "none", model_key)
        swap_result = swap_model(model_key)
        if not swap_result["ok"]:
            log.warning("Swap failed: %s — using currently loaded model", swap_result.get("error"))
            # Fall back to whatever is loaded
            detect_loaded_model()
            current = get_current_model()
            if current:
                model_name = current["name"]

    client = OllamaClient()
    answer = client.generate(user_prompt, system=system)

    sources = _format_sources(hits)

    # Stage 5: Citation verification
    citations = verify_citations(answer, sources)
    diagnostics["citations"] = {
        "total": citations["citation_count"],
        "verified": citations["verified_count"],
        "flagged": citations["flagged_count"],
    }
    diagnostics["model_key"] = model_key

    # Stage 5b: Enhanced confidence (uses citations + answer)
    confidence = _calculate_confidence(search_query, hits,
                                       citations=citations, answer_text=answer)

    # Stage 6: Source span mapping
    from rag.span_mapper import map_answer_to_sources
    answer_spans = map_answer_to_sources(answer, sources)

    # Stage 7: Parse structured answer
    from rag.reasoning import parse_structured_answer
    structured = parse_structured_answer(answer)

    return {
        "answer": answer,
        "structured_answer": structured,
        "sources": sources,
        "confidence": confidence,
        "model_used": model_name,
        "answer_mode": answer_mode,
        "citations": citations,
        "answer_spans": answer_spans,
        "diagnostics": diagnostics,
    }


def query_stream(user_query: str, top_k: int = 5, use_heavy_model: bool = False,
                 short_answer: bool = False, chat_history: list[dict] = None,
                 answer_mode: str = "balanced", reference_date: str | None = None,
                 doc_types: list[str] | None = None):
    """Streaming version with reranked retrieval and citation verification."""
    search_query = to_latin(user_query) if detect_script(user_query) == "cyrillic" else user_query
    query_class = classify_query(user_query)
    detected_domains = query_class.get("legal_domains", [])

    web_results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        rag_future = executor.submit(
            _retrieve_and_rerank, search_query, top_k, reference_date,
            doc_types, None, detected_domains
        )
        g_key = settings.google_api_key
        g_cx = settings.google_cx
        g_enabled = bool(g_key and g_cx)
        web_future = executor.submit(
            search_web, search_query, g_key, g_cx, g_enabled
        )
        hits, diagnostics = rag_future.result()
        try:
            web_results = web_future.result()
        except Exception:
            pass

    context = build_context(hits)
    web_context = format_web_context(web_results)
    if web_results:
        diagnostics["web_sources"] = len(web_results)

    system = get_system_prompt(answer_mode, short_answer)

    history_context = ""
    if chat_history:
        recent = chat_history[-6:]
        history_parts = []
        for msg in recent:
            role = "Korisnik" if msg["role"] == "user" else "LexArdor"
            history_parts.append(f"{role}: {msg['content'][:200]}")
        history_context = "\nPRETHODNA KONVERZACIJA:\n" + "\n".join(history_parts) + "\n"

    user_prompt = f"""PRAVNI IZVORI:
{context}
{web_context}
{history_context}
PITANJE KORISNIKA:
{user_query}

Odgovori na osnovu priloženih pravnih izvora. Ako postoje online izvori, koristi ih za dopunu i proveru.
Navedi relevantne članove zakona."""

    # Select model via router
    if use_heavy_model:
        target = get_active_reasoning_model()
    else:
        target = get_model_for_role("fast")

    current = get_current_model()
    model_key = target["key"]
    model_name = target["name"]

    if current is None or current["key"] != model_key:
        log.info("Auto-swap (stream): %s → %s", current["key"] if current else "none", model_key)
        swap_result = swap_model(model_key)
        if not swap_result["ok"]:
            log.warning("Swap failed: %s — using currently loaded model", swap_result.get("error"))
            detect_loaded_model()
            current = get_current_model()
            if current:
                model_name = current["name"]

    client = OllamaClient()
    diagnostics["model_key"] = model_key

    # For streaming, confidence is pre-calculated (no answer text yet)
    confidence = _calculate_confidence(search_query, hits)
    sources = _format_sources(hits)

    return {
        "stream": client.generate_stream(user_prompt, system=system),
        "sources": sources,
        "confidence": confidence,
        "model_used": model_name,
        "answer_mode": answer_mode,
        "diagnostics": diagnostics,
    }
