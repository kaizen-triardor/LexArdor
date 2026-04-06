"""Multi-stage legal reasoning pipeline with citation verification.

Sprint 3: Transforms raw retrieval into structured, citation-grounded
legal answers. Every claim maps to a source. Unsupported claims are flagged.

Stages:
1. Classify query (type, domain, complexity)
2. Retrieve + rerank (handled by pipeline.py)
3. Reason with structured output
4. Verify citations (post-generation check)
"""
import re
import json
import logging
from llm.ollama import OllamaClient
from core.config import settings

log = logging.getLogger("lexardor.reasoning")


# ── Stage 1: Query Classification ────────────────────────────────────────────

# Rule-based classification (fast, no LLM call needed)
DOMAIN_KEYWORDS = {
    "radno_pravo": ["rad", "radni", "zaposleni", "poslodavac", "otkaz", "plata", "odmor",
                     "ugovor o radu", "probni", "otkazni", "sindikat", "štrajk", "kolektivni"],
    "krivicno_pravo": ["krivičn", "kazna", "zatvor", "krađa", "prevara", "ubistv",
                        "krivično delo", "presuda", "tužilac", "optuženi", "pritvor"],
    "porodicno_pravo": ["brak", "razvod", "dete", "alimenta", "staratelj", "usvojenje",
                         "porodičn", "suprug", "nasledstv", "testament"],
    "obligaciono_pravo": ["ugovor", "šteta", "naknada", "odgovornost", "obligaci",
                           "raskid", "usluga", "garancija", "zakup", "zajam"],
    "poresko_pravo": ["porez", "porezk", "pdv", "dobit", "poreski", "poreska",
                       "doprinosi", "fiskalni", "budžet"],
    "privredno_pravo": ["privredno", "firma", "doo", "preduzeć", "osnivanje",
                         "likvidacija", "stečaj", "registracija", "apr"],
    "upravno_pravo": ["upravni", "inspekcija", "dozvola", "žalba", "rešenje",
                       "organ", "postupak"],
    "ustavno_pravo": ["ustav", "ustavni", "ljudska prava", "slobod", "diskriminacij"],
}

QUERY_TYPE_PATTERNS = {
    "factual": [r"koji\s", r"šta\s", r"koliko\s", r"da li\s", r"kakav\s", r"kakva\s"],
    "procedural": [r"kako\s", r"procedur", r"postupak", r"korak", r"potrebno je"],
    "analytical": [r"razlik", r"uporedi", r"prednost", r"nedostatak", r"rizik"],
    "hypothetical": [r"ako\s", r"ukoliko\s", r"da li bi", r"što bi bilo"],
}


def classify_query(query: str) -> dict:
    """Rule-based query classification — fast, no LLM needed."""
    q_lower = query.lower()

    # Detect legal domains
    domains = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            domains.append(domain)

    # Detect query type
    query_type = "factual"
    for qtype, patterns in QUERY_TYPE_PATTERNS.items():
        if any(re.search(p, q_lower) for p in patterns):
            query_type = qtype
            break

    # Estimate complexity
    word_count = len(query.split())
    complexity = "simple" if word_count < 15 else "moderate" if word_count < 40 else "complex"

    return {
        "query_type": query_type,
        "legal_domains": domains or ["opšte"],
        "complexity": complexity,
    }


# ── Stage 3: Structured Reasoning ────────────────────────────────────────────

SYSTEM_PROMPT_STRUCTURED = """Ti si LexArdor, AI pravni asistent za srpsko pravo.

PRAVILA:
1. UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). Nikada ne koristi engleski.
2. NE prikazuj svoje razmišljanje, analizu ili thought process. Samo daj odgovor.
3. Odgovaraj na osnovu priloženih izvora. Citiraj inline: "prema Članu 187 Zakona o radu..."
4. Počni sa DIREKTNIM odgovorom u 1-2 rečenice. Onda obrazloži.
5. NE pravi prazne sekcije. NE ponavljaj informacije. NE pravi tabele koje ponavljaju tekst.
6. Ako izvori ne pokrivaju pitanje — reci to u jednoj rečenici i preporuči advokata.
7. Nikad ne izmišljaj članove koji nisu u izvorima.
8. Završi sa jednom rečenicom napomene ako postoje rizici ili ograničenja.
9. Maksimalno 1000 karaktera. Budi precizan, ne razvlači."""

SYSTEM_PROMPT_CITIZEN = """Ti si LexArdor, AI pravni asistent koji pomaže građanima.
UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). NE prikazuj razmišljanje.

Objasni prosto i kratko, kao prijatelju. Navedi član zakona ali objasni šta znači.

FORMAT:
1. Odgovor u 1-2 rečenice (najvažnija stvar)
2. Šta kaže zakon (član + objašnjenje prostim rečima)
3. Šta da uradite dalje (jedan konkretan savet)

Maksimalno 500 karaktera. Ako ne znaš — reci "konsultujte advokata" i ništa više."""

SYSTEM_PROMPT_STRICT = """Ti si LexArdor, formalni pravni istraživač za srpsko pravo.
UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). NE prikazuj razmišljanje.

1. SAMO činjenice iz priloženih izvora. Nema spekulacija ni tumačenja.
2. Svaka tvrdnja ima citat: "Član X Zakona o Y propisuje da..."
3. Ako izvor ne pokriva pitanje: "Dostupni izvori ne sadrže odgovor na ovo pitanje."
4. Formalni pravnički stil. Bez filler teksta.
5. Maksimalno 800 karaktera."""


def parse_structured_answer(answer_text: str) -> dict:
    """Parse LLM output into 5 named sections.

    Looks for headers like KRATAK ODGOVOR:, PRAVNI OSNOV:, OBRAZLOŽENJE:, etc.
    Returns {sections: {name: text}, structured: bool, raw: str}.
    """
    section_names = [
        ("kratak_odgovor", r"(?:KRATAK\s+ODGOVOR|KRATKI?\s+ZAKLJUČAK|ZAKLJUČAK)\s*:?\s*"),
        ("pravni_osnov", r"(?:PRAVNI\s+OSNOV|PRAVNA\s+OSNOVA|RELEVANTNI?\s+PROPIS[I]?)\s*:?\s*"),
        ("obrazlozenje", r"(?:OBRAZLO[ŽZ]ENJE|DETALJN[OA]\s+ANALIZA|ANALIZA)\s*:?\s*"),
        ("rizici", r"(?:RIZICI?\s+I?\s*NAPOMENE?|NAPOMENE?|OGRANIČENJA?\s*(?:ANALIZE)?|RIZICI?)\s*:?\s*"),
        ("vaznost", r"(?:VA[ŽZ]NOST\s*(?:PROPISA)?|STATUS\s+VA[ŽZ]ENJA|TEMPORALNA?\s+ANALIZA)\s*:?\s*"),
    ]

    sections = {}
    remaining = answer_text

    # Try to split by section headers
    for key, pattern in section_names:
        match = re.search(pattern, remaining, re.IGNORECASE | re.MULTILINE)
        if match:
            # Find the start of content after the header
            start = match.end()
            # Find where the next section starts (or end of text)
            next_start = len(remaining)
            for _, next_pattern in section_names:
                if next_pattern == pattern:
                    continue
                next_match = re.search(next_pattern, remaining[start:], re.IGNORECASE | re.MULTILINE)
                if next_match:
                    next_start = min(next_start, start + next_match.start())
            sections[key] = remaining[start:next_start].strip()

    structured = len(sections) >= 2  # At least 2 sections found

    # Add PRAVNO MIŠLJENJE as alternative for strict mode
    if not structured:
        alt_match = re.search(r"PRAVNO\s+MI[ŠS]LJENJE\s*:?\s*", answer_text, re.IGNORECASE)
        if alt_match:
            sections["pravni_osnov"] = answer_text[alt_match.end():].strip()
            structured = True

    return {
        "sections": sections,
        "structured": structured,
        "raw": answer_text,
    }


def get_system_prompt(answer_mode: str, short_answer: bool = False) -> str:
    """Select system prompt based on answer mode."""
    if short_answer:
        return """Ti si LexArdor, AI pravni asistent za srpsko pravo.
UVEK odgovaraj ISKLJUČIVO na SRPSKOM jeziku (latinica). NE prikazuj razmišljanje.
Odgovaraj KRATKO — maksimalno 2-3 rečenice. Navedi samo ključan član zakona i zaključak.
Ako nemaš dovoljno informacija, reci to. Ne izmišljaj."""
    return {
        "strict": SYSTEM_PROMPT_STRICT,
        "citizen": SYSTEM_PROMPT_CITIZEN,
    }.get(answer_mode, SYSTEM_PROMPT_STRUCTURED)


# ── Stage 4: Citation Verification ───────────────────────────────────────────

def verify_citations(answer_text: str, sources: list[dict]) -> dict:
    """Post-generation check: verify cited articles exist in retrieved sources.

    Extracts article references from the LLM answer text, then checks
    each one against the actually retrieved sources.

    Returns: {
        "verified": [{"article": "179", "law": "Zakon o radu", "status": "verified"}],
        "flagged": [{"article": "999", "law": "Zakon o radu", "status": "unverified", "reason": "..."}],
        "citation_count": int,
        "verified_count": int,
        "flagged_count": int,
    }
    """
    # Extract article citations from answer text
    # Patterns: "Član 179", "član 24a", "čl. 15", "člana 37"
    citation_pattern = re.compile(
        r'(?:Član[auo]?m?|čl\.?|member)\s+(\d+[a-z]?)',
        re.IGNORECASE
    )

    cited_articles = []
    for m in citation_pattern.finditer(answer_text):
        art_num = m.group(1)
        # Try to find which law this citation refers to by looking at surrounding context
        context_start = max(0, m.start() - 100)
        context_end = min(len(answer_text), m.end() + 100)
        context = answer_text[context_start:context_end]

        # Try to extract law name from context
        law_match = re.search(r'Zakon[a-z]*\s+o\s+([^\.,\n]{3,40})', context, re.IGNORECASE)
        law_hint = law_match.group(0).strip() if law_match else ""

        cited_articles.append({
            "article": art_num,
            "law_hint": law_hint,
            "context": context.strip(),
        })

    # Build set of (article_number, law_slug) from retrieved sources
    source_articles = set()
    source_law_names = {}
    for s in sources:
        art = str(s.get("article", ""))
        slug = s.get("slug", "")
        law = s.get("law", "")
        source_articles.add((art, slug))
        source_articles.add((art, ""))  # Also match without specific law
        if slug:
            source_law_names[slug] = law

    verified = []
    flagged = []

    seen = set()
    for cite in cited_articles:
        art_num = cite["article"]
        if art_num in seen:
            continue
        seen.add(art_num)

        # Check if this article exists in any retrieved source
        found = False
        matched_law = ""
        for s in sources:
            if str(s.get("article", "")) == art_num:
                found = True
                matched_law = s.get("law", "")
                break

        if found:
            verified.append({
                "article": art_num,
                "law": matched_law,
                "status": "verified",
            })
        else:
            flagged.append({
                "article": art_num,
                "law": cite["law_hint"],
                "status": "unverified",
                "reason": "Ovaj član nije pronađen u pretraženim izvorima",
            })

    return {
        "verified": verified,
        "flagged": flagged,
        "citation_count": len(verified) + len(flagged),
        "verified_count": len(verified),
        "flagged_count": len(flagged),
    }


def verify_citations_with_llm(answer_text: str, sources: list[dict],
                                llm_client) -> dict:
    """LLM-powered citation verification — checks each claim against sources.

    More thorough than regex: catches fabricated claims, wrong law attribution,
    and claims without explicit article numbers.
    """
    if not sources:
        return {"verified": [], "flagged": [], "uncertain": [],
                "citation_count": 0, "verified_count": 0, "flagged_count": 0,
                "uncertain_count": 0, "method": "llm"}

    # Build source reference for the verifier
    source_ref = []
    for i, s in enumerate(sources[:8], 1):
        art = s.get("article", "?")
        law = s.get("law", s.get("law_raw", ""))
        text_preview = (s.get("full_text", "") or s.get("text", ""))[:300]
        source_ref.append(f"[Izvor {i}] {law} — Član {art}\n{text_preview}")
    sources_text = "\n\n".join(source_ref)

    prompt = f"""Proveri tačnost citata u sledećem pravnom odgovoru.

ODGOVOR ZA PROVERU:
{answer_text[:3000]}

DOSTUPNI IZVORI:
{sources_text}

ZADATAK: Za svaku tvrdnju u odgovoru koja se poziva na zakon ili član, proveri:
1. Da li je član zakona zaista prisutan u izvorima?
2. Da li odgovor tačno interpretira sadržaj tog člana?
3. Da li ima tvrdnji koje NEMAJU podršku ni u jednom izvoru?

ODGOVORI U JSON FORMATU:
{{
  "verified": [{{"claim": "kratka tvrdnja", "article": "179", "law": "Zakon o radu", "status": "verified"}}],
  "flagged": [{{"claim": "kratka tvrdnja", "article": "999", "law": "Nepoznat", "status": "unverified", "reason": "razlog"}}],
  "uncertain": [{{"claim": "kratka tvrdnja", "reason": "nedovoljno informacija"}}]
}}"""

    system = "Ti si pravni verifikator. Proveravaj STROGO — svaka tvrdnja mora imati podršku u izvorima."

    try:
        response = llm_client.generate(prompt, system=system, temperature=0.1, max_tokens=1500)
        result = _parse_verification_response(response)
        result["method"] = "llm"
        return result
    except Exception as e:
        # Fall back to regex verification
        fallback = verify_citations(answer_text, sources)
        fallback["method"] = "regex_fallback"
        fallback["llm_error"] = str(e)
        return fallback


def _parse_verification_response(response: str) -> dict:
    """Parse LLM verification response into structured format."""
    try:
        match = re.search(r'\{[\s\S]*\}', response)
        if match:
            raw = match.group()
            raw = re.sub(r',\s*}', '}', raw)
            raw = re.sub(r',\s*]', ']', raw)
            data = json.loads(raw)
            verified = data.get("verified", [])
            flagged = data.get("flagged", [])
            uncertain = data.get("uncertain", [])
            return {
                "verified": verified,
                "flagged": flagged,
                "uncertain": uncertain,
                "citation_count": len(verified) + len(flagged) + len(uncertain),
                "verified_count": len(verified),
                "flagged_count": len(flagged),
                "uncertain_count": len(uncertain),
            }
    except (json.JSONDecodeError, AttributeError) as e:
        log.warning("Failed to parse LLM citation verification response: %s", e)
    return {"verified": [], "flagged": [], "uncertain": [],
            "citation_count": 0, "verified_count": 0, "flagged_count": 0,
            "uncertain_count": 0}
