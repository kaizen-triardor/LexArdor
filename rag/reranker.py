"""Cross-encoder reranker for legal retrieval quality.

Uses ms-marco-MiniLM-L-6-v2 (~80MB, CPU) to rescore (query, article) pairs
after initial vector retrieval. This is the single highest-impact
retrieval improvement — eliminates false positives from embedding space.
"""
from __future__ import annotations
from sentence_transformers import CrossEncoder

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    """Lazy-load cross-encoder model (downloads ~80MB on first use)."""
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
    return _reranker


def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """Rerank retrieval candidates using cross-encoder scoring.

    Args:
        query: The user's search query
        candidates: List of dicts with at least 'text' or 'document' key
        top_k: Number of top results to return

    Returns:
        Top-k candidates sorted by cross-encoder score, each with added 'rerank_score'
    """
    if not candidates:
        return []

    model = _get_reranker()

    # Build (query, document) pairs for scoring
    pairs = []
    for c in candidates:
        doc_text = c.get("text", "") or c.get("document", "") or ""
        # Truncate very long articles to avoid wasting compute
        if len(doc_text) > 1500:
            doc_text = doc_text[:1500]
        pairs.append((query, doc_text))

    # Score all pairs
    scores = model.predict(pairs, show_progress_bar=False)

    # Attach scores and sort
    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])

    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
    return candidates[:top_k]
