"""Cross-reference graph expansion for legal retrieval.

After initial retrieval, looks up articles referenced by the top results
and adds them to the candidate set. This ensures the LLM has the full
legal picture (e.g., if Član 179 references Član 79, include Član 79).
"""
from __future__ import annotations
from db.legal_schema import get_legal_db


def expand_with_cross_refs(
    top_chroma_ids: list[str],
    max_refs: int = 5,
) -> list[str]:
    """Given ChromaDB IDs of top hits, find cross-referenced articles.

    Returns list of ChromaDB IDs for referenced articles (not already in top hits).
    """
    if not top_chroma_ids:
        return []

    conn = get_legal_db()
    expanded_ids = []
    seen = set(top_chroma_ids)

    for chroma_id in top_chroma_ids:
        # Find article in SQLite by chroma_id
        art = conn.execute(
            "SELECT id, document_id FROM legal_articles WHERE chroma_id = ?",
            (chroma_id,)
        ).fetchone()
        if not art:
            continue

        # Get outgoing cross-references
        refs = conn.execute("""
            SELECT ce.target_document_slug, ce.target_article_number
            FROM citation_edges ce
            WHERE ce.source_article_id = ?
            LIMIT 10
        """, (art["id"],)).fetchall()

        for ref in refs:
            target_slug = ref["target_document_slug"]
            target_art = ref["target_article_number"]
            if not target_slug or not target_art:
                continue

            # Build expected chroma_id
            ref_chroma_id = f"{target_slug}_clan_{target_art}"
            if ref_chroma_id not in seen:
                seen.add(ref_chroma_id)
                expanded_ids.append(ref_chroma_id)

            if len(expanded_ids) >= max_refs:
                break

        if len(expanded_ids) >= max_refs:
            break

    conn.close()
    return expanded_ids


def fetch_articles_by_chroma_ids(chroma_ids: list[str]) -> list[dict]:
    """Fetch article data from ChromaDB by their IDs."""
    if not chroma_ids:
        return []

    from rag.store import get_collection
    collection = get_collection()

    try:
        result = collection.get(
            ids=chroma_ids,
            include=["documents", "metadatas"],
        )
    except Exception:
        return []

    articles = []
    for i, doc_id in enumerate(result["ids"]):
        articles.append({
            "id": doc_id,
            "text": result["documents"][i],
            "document": result["documents"][i],
            "metadata": result["metadatas"][i],
            "score": 0.5,  # Neutral score — reranker will rescore
            "vec_score": 0.0,
            "keyword_boost": 0.0,
            "authority_boost": 0.0,
            "source_collection": "core_laws",
            "is_cross_ref": True,
        })
    return articles
