"""Autonomous legal research agent.

Performs multi-step research: generates a research plan, executes multiple
searches across the corpus, aggregates evidence, and produces a comprehensive
research report.

Flow:
1. Analyze research topic -> generate search plan (3-6 queries)
2. Execute each query against RAG pipeline
3. Deduplicate and rank all found sources
4. Synthesize findings into a structured research report
"""
import json
import logging
import re
import time

from llm.ollama import OllamaClient
from rag.store import search_with_filters
from rag.reranker import rerank
from rag.pipeline import _format_sources
from core.transliterate import to_latin, detect_script

log = logging.getLogger("lexardor.agent")


# -- Query Decomposition Prompt -----------------------------------------------

DECOMPOSE_SYSTEM = """Ti si pravni istraživač. Tvoj zadatak je da razložiš istraživačku temu na konkretne pretrage pravne baze.

PRAVILA:
1. Generiši 3-6 specifičnih upita za pretragu
2. Svaki upit mora biti fokusiran na jedan pravni aspekt teme
3. Uključi i opšte i specifične upite (npr. i "prava zaposlenih" i "otkazni rok")
4. Pokrij različite uglove: definicije, prava, obaveze, postupak, rokovi, sankcije

ODGOVORI U JSON FORMATU:
{
  "queries": [
    {"query": "tekst upita za pretragu", "aspect": "kratko koji aspekt pokriva"}
  ],
  "topic_summary": "kratko rezime teme istraživanja"
}"""


def _decompose_topic(topic: str, max_queries: int, llm: OllamaClient) -> list[dict]:
    """Use LLM to decompose a research topic into specific search queries.

    Returns list of {"query": str, "aspect": str} dicts.
    Falls back to simple keyword decomposition if LLM fails.
    """
    prompt = f"""TEMA ISTRAŽIVANJA: {topic}

Generiši {max_queries} specifičnih upita za pretragu pravne baze koji pokrivaju sve aspekte ove teme.
Upiti treba da budu na srpskom jeziku, fokusirani i konkretni."""

    try:
        response = llm.generate(prompt, system=DECOMPOSE_SYSTEM,
                                temperature=0.2, max_tokens=800)
        match = re.search(r'\{[\s\S]*\}', response)
        if match:
            raw = match.group()
            raw = re.sub(r',\s*}', '}', raw)
            raw = re.sub(r',\s*]', ']', raw)
            parsed = json.loads(raw)
            queries = parsed.get("queries", [])
            if queries and len(queries) >= 2:
                return queries[:max_queries]
    except Exception as e:
        log.warning("Topic decomposition failed: %s — using fallback", e)

    # Fallback: split topic into overlapping queries
    return _fallback_decompose(topic, max_queries)


def _fallback_decompose(topic: str, max_queries: int) -> list[dict]:
    """Simple fallback decomposition when LLM is unavailable."""
    queries = [
        {"query": topic, "aspect": "osnovno pitanje"},
    ]
    # Add keyword-based variants
    words = topic.split()
    if len(words) > 4:
        mid = len(words) // 2
        queries.append({"query": " ".join(words[:mid]), "aspect": "prvi deo"})
        queries.append({"query": " ".join(words[mid:]), "aspect": "drugi deo"})
    if len(words) > 2:
        # Add a focused query with just the nouns (crude heuristic)
        queries.append({"query": " ".join(w for w in words if len(w) > 3),
                         "aspect": "ključni termini"})
    return queries[:max_queries]


# -- Synthesis Prompt ----------------------------------------------------------

SYNTHESIS_SYSTEM = """Ti si LexArdor, AI pravni istraživač specijalizovan za srpsko pravo.

ZADATAK: Na osnovu svih prikupljenih pravnih izvora, napiši SVEOBUHVATAN istraživački izveštaj.

OBAVEZNA STRUKTURA IZVEŠTAJA:

REZIME:
(Kratko rezime najvažnijih nalaza — 2-3 rečenice)

PRAVNI OKVIR:
(Koji zakoni i propisi regulišu ovu oblast)

DETALJNA ANALIZA:
(Sistematičan pregled svih relevantnih odredbi sa citatima. Organizuj po tematskim celinama.)

KLJUČNI ČLANOVI:
(Taksativna lista svih relevantnih članova zakona sa kratkim opisom)

PRAKTIČNE IMPLIKACIJE:
(Šta ovo znači u praksi — rokovi, postupci, prava i obaveze)

OGRANIČENJA ANALIZE:
(Šta nije pokriveno, koji aspekti zahtevaju dodatno istraživanje)

PRAVILA:
1. SVAKA tvrdnja mora imati citat (Član X Zakona o Y)
2. Ako izvor ne pokriva neki aspekt, NAVEDI TO
3. Razlikuj važeće i nevažeće propise
4. Koristi formalni pravnički stil ali budi razumljiv
5. Organizuj nalaze logično — od opšteg ka posebnom"""


def _synthesize_report(topic: str, all_hits: list[dict], queries_executed: list[dict],
                       llm: OllamaClient) -> str:
    """Synthesize a comprehensive research report from all gathered evidence."""
    # Build evidence context from all hits
    parts = []
    for i, hit in enumerate(all_hits, 1):
        meta = hit.get("metadata", {})
        law = meta.get("law_title", meta.get("law_slug", ""))
        art = meta.get("article_number", "?")
        gazette = meta.get("gazette", "")
        parts.append(f"[Izvor {i}] {law} — Član {art} ({gazette})\n{hit['text']}\n")
    context = "\n".join(parts) if parts else "Nema pronađenih relevantnih pravnih izvora."

    # Include query coverage info
    queries_info = "\n".join(
        f"- {q['query']} ({q.get('aspect', '')}): {q.get('results_count', 0)} rezultata"
        for q in queries_executed
    )

    prompt = f"""TEMA ISTRAŽIVANJA: {topic}

IZVRŠENI UPITI:
{queries_info}

PRIKUPLJENI PRAVNI IZVORI ({len(all_hits)} ukupno):
{context}

Na osnovu svih prikupljenih izvora, napiši sveobuhvatan istraživački izveštaj o zadatoj temi."""

    report = llm.generate(prompt, system=SYNTHESIS_SYSTEM, max_tokens=3000)
    return report


# -- Main Research Function ----------------------------------------------------

def research(topic: str, max_queries: int = 5,
             reference_date: str | None = None) -> dict:
    """Autonomous legal research agent.

    Decomposes a topic into multiple search queries, executes each against the
    RAG pipeline, deduplicates results, and synthesizes a comprehensive report.

    Args:
        topic: Research topic in natural language (Serbian).
        max_queries: Maximum number of search queries to generate (3-6).
        reference_date: ISO date for temporal filtering (YYYY-MM-DD).

    Returns:
        {
            "report": str,          # Comprehensive research report
            "sources": list[dict],  # All deduplicated sources (formatted)
            "queries_executed": list[dict],  # Each query with results count
            "per_query_results": dict,       # Results keyed by query index
            "diagnostics": dict,    # Timing and stats
        }
    """
    t0 = time.time()
    max_queries = max(3, min(max_queries, 6))

    # Transliterate topic if Cyrillic
    search_topic = to_latin(topic) if detect_script(topic) == "cyrillic" else topic

    llm = OllamaClient()

    # -- Step 1: Decompose topic into search queries --
    t1 = time.time()
    queries = _decompose_topic(search_topic, max_queries, llm)
    decompose_time = round(time.time() - t1, 2)
    log.info("Research agent: decomposed into %d queries (%.2fs)", len(queries), decompose_time)

    # -- Step 2: Execute each query --
    t2 = time.time()
    all_candidates = []
    seen_ids: set[str] = set()
    per_query_results: dict[int, list[dict]] = {}

    for i, q in enumerate(queries):
        search_q = q["query"]
        # Transliterate if needed
        if detect_script(search_q) == "cyrillic":
            search_q = to_latin(search_q)

        candidates = search_with_filters(search_q, top_k=10, fetch_k=30,
                                         reference_date=reference_date)

        # Rerank candidates for this query
        reranked = rerank(search_q, candidates, top_k=8)

        q["results_count"] = len(reranked)
        per_query_results[i] = []

        for c in reranked:
            per_query_results[i].append({
                "id": c["id"],
                "score": round(c.get("rerank_score", c["score"]), 3),
                "law": c.get("metadata", {}).get("law_title", ""),
                "article": c.get("metadata", {}).get("article_number", ""),
            })
            # Deduplicate across queries
            if c["id"] not in seen_ids:
                seen_ids.add(c["id"])
                all_candidates.append(c)

    search_time = round(time.time() - t2, 2)
    log.info("Research agent: searched %d queries, %d unique candidates (%.2fs)",
             len(queries), len(all_candidates), search_time)

    # -- Step 3: Global rerank of all deduplicated candidates --
    t3 = time.time()
    global_reranked = rerank(search_topic, all_candidates, top_k=15)
    rerank_time = round(time.time() - t3, 2)

    # -- Step 4: Synthesize report --
    t4 = time.time()
    report = _synthesize_report(topic, global_reranked, queries, llm)
    synthesis_time = round(time.time() - t4, 2)

    # -- Format sources --
    sources = _format_sources(global_reranked)

    total_time = round(time.time() - t0, 2)
    log.info("Research agent: complete in %.2fs (%d sources)", total_time, len(sources))

    return {
        "report": report,
        "sources": sources,
        "queries_executed": queries,
        "per_query_results": per_query_results,
        "diagnostics": {
            "total_time_s": total_time,
            "decompose_time_s": decompose_time,
            "search_time_s": search_time,
            "rerank_time_s": rerank_time,
            "synthesis_time_s": synthesis_time,
            "total_queries": len(queries),
            "total_candidates": len(all_candidates),
            "final_sources": len(sources),
        },
    }
