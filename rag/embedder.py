"""Multilingual embeddings using sentence-transformers (intfloat/multilingual-e5-base)."""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from core.config import settings

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed passages — each text is prefixed with 'passage: ' per E5 convention."""
    prefixed = [f"passage: {t}" for t in texts]
    model = get_model()
    embeddings = model.encode(prefixed, normalize_embeddings=True)
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """Embed a single query — prefixed with 'query: ' per E5 convention."""
    model = get_model()
    embedding = model.encode(f"query: {query}", normalize_embeddings=True)
    return embedding.tolist()
