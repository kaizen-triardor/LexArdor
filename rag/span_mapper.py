"""Source span mapping — link answer sentences to their supporting sources.

Phase 1 (regex): Extract article references from each answer sentence,
match to retrieved source by article number.

Phase 2 (future): Embedding similarity per sentence for non-citation claims.
"""
import re


# Sentence splitter for Serbian legal text
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-ZČĆŽŠĐ])')

# Citation patterns: "Član 179", "čl. 15", "člana 37", "stavom 2"
_CITE_PATTERN = re.compile(
    r'(?:Član[auo]?m?|čl\.?)\s+(\d+[a-z]?)',
    re.IGNORECASE
)

# Law name pattern near citation
_LAW_PATTERN = re.compile(
    r'Zakon[a-z]*\s+o\s+([^\.,\n]{3,40})',
    re.IGNORECASE
)


def map_answer_to_sources(answer_text: str, sources: list[dict]) -> list[dict]:
    """Map each answer sentence to the source(s) that support it.

    Returns list of:
    {
        "sentence": str,
        "sentence_idx": int,
        "source_refs": [{"source_idx": int, "article": str, "law": str, "confidence": str}],
    }
    """
    if not answer_text or not sources:
        return []

    # Build lookup: article_number → source indices
    article_to_sources = {}
    for i, src in enumerate(sources):
        art = str(src.get("article", ""))
        if art:
            article_to_sources.setdefault(art, []).append(i)

    # Split answer into sentences
    sentences = _SENTENCE_SPLIT.split(answer_text.strip())
    if not sentences:
        return []

    result = []
    for idx, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue

        refs = []
        # Find article citations in this sentence
        for m in _CITE_PATTERN.finditer(sentence):
            art_num = m.group(1)
            matched_sources = article_to_sources.get(art_num, [])

            # Try to narrow by law name
            law_match = _LAW_PATTERN.search(sentence)
            law_hint = law_match.group(0).strip().lower() if law_match else ""

            for src_idx in matched_sources:
                src = sources[src_idx]
                src_law = (src.get("law", "") or "").lower()

                # Check law name match if we have a hint
                if law_hint and src_law:
                    # Fuzzy match: check if key words overlap
                    hint_words = set(law_hint.split())
                    law_words = set(src_law.split())
                    overlap = hint_words & law_words
                    confidence = "high" if len(overlap) >= 2 else "medium"
                else:
                    confidence = "medium" if len(matched_sources) == 1 else "low"

                refs.append({
                    "source_idx": src_idx,
                    "article": art_num,
                    "law": src.get("law", ""),
                    "confidence": confidence,
                })

        # Deduplicate by source_idx
        seen = set()
        unique_refs = []
        for r in refs:
            if r["source_idx"] not in seen:
                seen.add(r["source_idx"])
                unique_refs.append(r)

        result.append({
            "sentence": sentence,
            "sentence_idx": idx,
            "source_refs": unique_refs,
            "has_citation": len(unique_refs) > 0,
        })

    return result
