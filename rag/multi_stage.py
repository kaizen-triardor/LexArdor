"""Multi-stage legal reasoning pipeline.

4-stage architecture for complex legal questions:
1. Planner (fast model) — decompose query into sub-questions + search plan
2. Researcher (no LLM) — run retrieval for each sub-question
3. Synthesizer (reasoning model) — generate structured answer from all evidence
4. Critic (fast model) — check consistency, flag unsupported claims

Key constraint: single GPU = one model at a time.
Design: Planner + Critic use fast model (same), Synthesizer uses reasoning model.
Result: maximum 1 model swap per query (fast → reasoning).
"""
import json
import logging
import re
import time

from llm.ollama import OllamaClient
from rag.store import search_with_filters
from rag.reranker import rerank
from rag.graph import expand_with_cross_refs, fetch_articles_by_chroma_ids
from rag.reasoning import classify_query, get_system_prompt, verify_citations
from core.transliterate import to_latin, detect_script

log = logging.getLogger("lexardor.multi_stage")


# ── Stage 1: Planner ────────────────────────────────────────────────────────

PLANNER_SYSTEM = """Ti si pravni planer. Odgovaraj ISKLJUČIVO na srpskom jeziku.
Tvoj zadatak je da razložiš korisnikovo pitanje na potpitanja za pretragu pravne baze.

PRAVILA:
1. Razloži pitanje na 2-4 potpitanja za pretragu
2. Za svako potpitanje navedi ključne pravne termine za pretragu
3. Identifikuj oblast prava (radno, krivično, porodično, obligaciono, poresko, privredno, upravno, ustavno)

ODGOVORI U JSON FORMATU:
{
  "sub_questions": [
    {"question": "potpitanje", "search_terms": ["term1", "term2"], "domain": "oblast_prava"}
  ],
  "complexity": "simple|moderate|complex",
  "needs_comparison": false,
  "needs_timeline": false
}"""


def _plan(query: str, query_class: dict, llm: OllamaClient) -> dict:
    """Stage 1: Decompose query into sub-questions."""
    t0 = time.time()

    prompt = f"""PITANJE KORISNIKA: {query}

KLASIFIKACIJA: tip={query_class.get('query_type', 'factual')}, domen={query_class.get('legal_domains', ['opšte'])}, složenost={query_class.get('complexity', 'simple')}

Razloži ovo pitanje na potpitanja za pretragu pravne baze."""

    try:
        response = llm.generate(prompt, system=PLANNER_SYSTEM, temperature=0.1, max_tokens=800)
        match = re.search(r'\{[\s\S]*\}', response)
        if match:
            raw = match.group()
            raw = re.sub(r',\s*}', '}', raw)
            raw = re.sub(r',\s*]', ']', raw)
            plan = json.loads(raw)
        else:
            plan = _default_plan(query, query_class)
    except Exception as e:
        log.warning("Planner failed: %s — using default plan", e)
        plan = _default_plan(query, query_class)

    plan["_time_s"] = round(time.time() - t0, 2)
    return plan


def _default_plan(query: str, query_class: dict) -> dict:
    """Fallback plan when LLM planner fails."""
    return {
        "sub_questions": [
            {"question": query, "search_terms": query.split()[:5],
             "domain": (query_class.get("legal_domains") or ["opšte"])[0]}
        ],
        "complexity": query_class.get("complexity", "simple"),
        "needs_comparison": False,
        "needs_timeline": False,
    }


# ── Stage 2: Researcher ─────────────────────────────────────────────────────

def _research(plan: dict, original_query: str, top_k: int = 5,
              reference_date: str | None = None) -> tuple[list[dict], dict]:
    """Stage 2: Run retrieval for each sub-question and merge results."""
    t0 = time.time()
    all_candidates = []
    seen_ids = set()

    sub_questions = plan.get("sub_questions", [{"question": original_query}])

    for sq in sub_questions:
        search_q = sq.get("question", original_query)
        # Transliterate if needed
        if detect_script(search_q) == "cyrillic":
            search_q = to_latin(search_q)

        candidates = search_with_filters(search_q, top_k=top_k, fetch_k=30,
                                         reference_date=reference_date)

        for c in candidates:
            if c["id"] not in seen_ids:
                seen_ids.add(c["id"])
                all_candidates.append(c)

    # Cross-reference expansion
    top_ids = [c["id"] for c in all_candidates[:15]]
    ref_ids = expand_with_cross_refs(top_ids, max_refs=5)
    if ref_ids:
        ref_articles = fetch_articles_by_chroma_ids(ref_ids)
        for a in ref_articles:
            if a["id"] not in seen_ids:
                seen_ids.add(a["id"])
                all_candidates.append(a)

    # Rerank all candidates against original query
    reranked = rerank(original_query, all_candidates, top_k=top_k * 2)

    diagnostics = {
        "sub_questions": len(sub_questions),
        "candidates_total": len(all_candidates),
        "cross_refs_added": len(ref_ids) if ref_ids else 0,
        "reranked_count": len(reranked),
        "_time_s": round(time.time() - t0, 2),
    }

    return reranked, diagnostics


# ── Stage 3: Synthesizer ────────────────────────────────────────────────────

SYNTHESIZER_SYSTEM = """Ti si LexArdor, AI pravni ekspert za srpsko pravo. Ovo je temeljita 4-fazna analiza.
UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). NE prikazuj razmišljanje ili thought process.

Na osnovu svih priloženih izvora, napiši sveobuhvatan odgovor:
1. Počni sa direktnim odgovorom (1-2 rečenice)
2. Citiraj članove inline: "prema Članu X Zakona o Y..."
3. Analiziraj sve relevantne izvore, ne ponavljaj iste informacije
4. Ako postoje suprotstavljena tumačenja ili rizici — navedi ih kratko
5. Svaka tvrdnja MORA imati citat iz izvora
6. Ako izvor ne pokriva pitanje — reci to jasno u jednoj rečenici
7. Maksimalno 1500 karaktera za temeljitu analizu"""


def _synthesize(query: str, hits: list[dict], plan: dict,
                answer_mode: str, chat_history: list[dict] | None,
                llm: OllamaClient) -> tuple[str, float]:
    """Stage 3: Generate structured answer from evidence."""
    t0 = time.time()

    # Build evidence context
    parts = []
    for i, hit in enumerate(hits, 1):
        meta = hit.get("metadata", {})
        law = meta.get("law_title", meta.get("law_slug", ""))
        art = meta.get("article_number", "?")
        gazette = meta.get("gazette", "")
        parts.append(f"[Izvor {i}] {law} — Član {art} ({gazette})\n{hit['text']}\n")
    context = "\n".join(parts) if parts else "Nema pronađenih relevantnih pravnih izvora."

    # Use structured or mode-specific system prompt
    system = SYNTHESIZER_SYSTEM if answer_mode == "strict" else get_system_prompt(answer_mode, False)

    # History context
    history_context = ""
    if chat_history:
        recent = chat_history[-6:]
        history_parts = [f"{'Korisnik' if m['role'] == 'user' else 'LexArdor'}: {m['content'][:200]}"
                         for m in recent]
        history_context = "\nPRETHODNA KONVERZACIJA:\n" + "\n".join(history_parts) + "\n"

    # Sub-questions from plan for context
    sub_qs = plan.get("sub_questions", [])
    plan_context = ""
    if len(sub_qs) > 1:
        plan_context = "\nANALIZA PITANJA:\n" + "\n".join(
            f"- {sq.get('question', '')}" for sq in sub_qs
        ) + "\n"

    prompt = f"""PRAVNI IZVORI:
{context}
{history_context}{plan_context}
PITANJE KORISNIKA:
{query}

Odgovori sveobuhvatno koristeći SVE priložene pravne izvore. Navedi relevantne članove zakona."""

    answer = llm.generate(prompt, system=system, max_tokens=3000)
    elapsed = time.time() - t0
    return answer, elapsed


# ── Stage 4: Critic ──────────────────────────────────────────────────────────

CRITIC_SYSTEM = """Ti si pravni recenzent. Odgovaraj ISKLJUČIVO na srpskom jeziku.
Pregledaj odgovor i identifikuj probleme.

Proveri:
1. Da li su SVI navedeni članovi zakona zaista prisutni u izvorima?
2. Da li ima tvrdnji BEZ citata?
3. Da li je odgovor konzistentan (nema protivrečnosti)?
4. Da li nedostaju važne napomene ili rizici?

ODGOVORI U JSON FORMATU:
{
  "quality": "high|medium|low",
  "issues": ["opis problema 1", "opis problema 2"],
  "unsupported_claims": ["tvrdnja bez izvora"],
  "missing_aspects": ["aspekt koji nedostaje"],
  "suggestion": "kratka preporuka za poboljšanje"
}"""


def _critique(query: str, answer: str, sources_text: str,
              llm: OllamaClient) -> dict:
    """Stage 4: Review answer for consistency and unsupported claims."""
    t0 = time.time()

    prompt = f"""PITANJE: {query}

ODGOVOR ZA RECENZIJU:
{answer[:2000]}

DOSTUPNI IZVORI (sažetak):
{sources_text[:2000]}

Oceni kvalitet ovog pravnog odgovora."""

    try:
        response = llm.generate(prompt, system=CRITIC_SYSTEM, temperature=0.1, max_tokens=800)
        match = re.search(r'\{[\s\S]*\}', response)
        if match:
            raw = match.group()
            raw = re.sub(r',\s*}', '}', raw)
            raw = re.sub(r',\s*]', ']', raw)
            critique = json.loads(raw)
        else:
            critique = {"quality": "unknown", "issues": []}
    except Exception as e:
        log.warning("Critic failed: %s", e)
        critique = {"quality": "unknown", "issues": [], "error": str(e)}

    critique["_time_s"] = round(time.time() - t0, 2)
    return critique


# ── Orchestrator ─────────────────────────────────────────────────────────────

def query_deep(user_query: str, top_k: int = 8, chat_history: list[dict] = None,
               answer_mode: str = "strict", reference_date: str | None = None,
               progress_callback=None) -> dict:
    """Full 4-stage multi-stage reasoning pipeline.

    Args:
        progress_callback: Optional callable(stage: int, total: int, message: str)
            for UI progress reporting.

    Returns same structure as pipeline.query() plus critique and stage timings.
    """
    from rag.pipeline import _format_sources, _calculate_confidence
    from llm.model_router import get_current_model, get_model_for_role, swap_model

    def _progress(stage, msg):
        if progress_callback:
            progress_callback(stage, 4, msg)
        log.info("Stage %d/4: %s", stage, msg)

    # ── Stage 1: Plan ──
    _progress(1, "Planiranje pretrage...")
    query_class = classify_query(user_query)

    # Use whatever model is currently loaded for planning
    llm = OllamaClient()
    plan = _plan(user_query, query_class, llm)

    # ── Stage 2: Research ──
    _progress(2, f"Pretraga ({len(plan.get('sub_questions', []))} potpitanja)...")
    search_query = to_latin(user_query) if detect_script(user_query) == "cyrillic" else user_query
    hits, research_diag = _research(plan, search_query, top_k=top_k,
                                     reference_date=reference_date)

    # ── Stage 3: Synthesize ──
    _progress(3, "Sinteza odgovora...")
    # Check if we need to swap to reasoning model
    current = get_current_model()
    reasoning = get_model_for_role("reasoning")
    if current and current["key"] != reasoning["key"]:
        swap_result = swap_model(reasoning["key"])
        if not swap_result["ok"]:
            log.warning("Could not swap to reasoning model: %s", swap_result.get("error"))

    llm_synth = OllamaClient()
    answer, synth_time = _synthesize(user_query, hits, plan, answer_mode,
                                      chat_history, llm_synth)

    # ── Stage 4: Critic ──
    _progress(4, "Provera kvaliteta...")
    # Swap back to fast model for critic (if possible)
    fast = get_model_for_role("fast")
    if current and current["key"] == fast["key"]:
        swap_model(fast["key"])

    llm_critic = OllamaClient()
    sources_summary = "\n".join(
        f"[{i+1}] {h.get('metadata', {}).get('law_title', '')} — Član {h.get('metadata', {}).get('article_number', '?')}"
        for i, h in enumerate(hits[:8])
    )
    critique = _critique(user_query, answer, sources_summary, llm_critic)

    # ── Assemble results ──
    sources = _format_sources(hits)
    citations = verify_citations(answer, sources)
    confidence = _calculate_confidence(search_query, hits,
                                       citations=citations, answer_text=answer)

    diagnostics = {
        "query_class": query_class,
        "plan": plan,
        "research": research_diag,
        "synthesis_time_s": round(synth_time, 2),
        "critique": critique,
        "multi_stage": True,
        "citations": {
            "total": citations["citation_count"],
            "verified": citations["verified_count"],
            "flagged": citations["flagged_count"],
        },
    }

    # Adjust confidence based on critique (keep dict structure)
    if critique.get("quality") == "low":
        confidence["level"] = "low"
        confidence["red_flags"].append("Recenzent ocenio kvalitet kao nizak")
    elif critique.get("quality") == "medium" and confidence.get("level") == "high":
        confidence["level"] = "medium"
        confidence["reasons"].append("Recenzent snizio ocenu na umerenu")

    # Source span mapping
    from rag.span_mapper import map_answer_to_sources
    answer_spans = map_answer_to_sources(answer, sources)

    return {
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
        "model_used": reasoning.get("name", "unknown"),
        "answer_mode": answer_mode,
        "citations": citations,
        "answer_spans": answer_spans,
        "critique": critique,
        "diagnostics": diagnostics,
    }
