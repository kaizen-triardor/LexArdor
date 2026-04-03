"""Tests for the RAG embedder and ChromaDB vector store."""

import pytest

from rag import embedder, store
from core.config import settings


SAMPLE_LAW = {
    "slug": "zakon-o-zastiti-zivotne-sredine",
    "title": "Zakon o zaštiti životne sredine",
    "gazette": "Sl. glasnik RS, br. 135/2004",
    "source_url": "https://www.paragraf.rs/propisi/zakon-o-zastiti-zivotne-sredine.html",
    "articles": [
        {
            "number": "1",
            "text": "Ovim zakonom uređuje se integralni sistem zaštite životne sredine.",
            "chapter": "I. Osnovne odredbe",
        },
        {
            "number": "2",
            "text": "Zaštita životne sredine obuhvata mere zaštite od zagađivanja vazduha, vode i zemljišta.",
            "chapter": "I. Osnovne odredbe",
        },
        {
            "number": "3",
            "text": "Inspekcija za zaštitu životne sredine vrši nadzor nad primenom ovog zakona.",
            "chapter": "II. Inspekcijski nadzor",
        },
    ],
}


@pytest.fixture(autouse=True)
def _use_temp_chroma(tmp_path, monkeypatch):
    """Point ChromaDB to a temp directory and reset store singletons."""
    monkeypatch.setattr(settings, "chroma_path", str(tmp_path / "chroma"))
    # Reset the module-level singletons so the new path is picked up
    store._client = None
    store._collection = None
    store._client_collection = None
    yield
    store._client = None
    store._collection = None
    store._client_collection = None


class TestEmbedder:
    def test_embed_texts_returns_list_of_vectors(self):
        vecs = embedder.embed_texts(["Hello world", "Zdravo svete"])
        assert len(vecs) == 2
        assert isinstance(vecs[0], list)
        assert len(vecs[0]) > 0

    def test_embed_query_returns_single_vector(self):
        vec = embedder.embed_query("životna sredina")
        assert isinstance(vec, list)
        assert len(vec) > 0

    def test_embeddings_are_normalized(self):
        import math

        vec = embedder.embed_query("test normalization")
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-3


class TestStore:
    def test_ingest_and_stats(self):
        count = store.ingest_law(SAMPLE_LAW)
        assert count == 3
        stats = store.get_stats()
        assert stats["total_articles"] == 3

    def test_ingest_empty_law(self):
        law = {**SAMPLE_LAW, "articles": []}
        assert store.ingest_law(law) == 0

    def test_search_returns_results(self):
        store.ingest_law(SAMPLE_LAW)
        results = store.search("zagađivanje vazduha i vode")
        assert len(results) > 0
        top = results[0]
        assert "id" in top
        assert "text" in top
        assert "metadata" in top
        assert "score" in top
        assert 0.0 <= top["score"] <= 1.0

    def test_search_with_law_filter(self):
        store.ingest_law(SAMPLE_LAW)
        results = store.search("inspekcija", law_filter="zakon-o-zastiti-zivotne-sredine")
        assert len(results) > 0
        assert all(
            r["metadata"]["law_slug"] == "zakon-o-zastiti-zivotne-sredine"
            for r in results
        )

    def test_search_wrong_filter_returns_empty(self):
        store.ingest_law(SAMPLE_LAW)
        results = store.search("inspekcija", law_filter="nepostojeci-zakon")
        assert len(results) == 0

    def test_upsert_idempotent(self):
        store.ingest_law(SAMPLE_LAW)
        store.ingest_law(SAMPLE_LAW)
        stats = store.get_stats()
        assert stats["total_articles"] == 3

    def test_document_format(self):
        store.ingest_law(SAMPLE_LAW)
        results = store.search("integralni sistem zaštite")
        found = [r for r in results if r["id"] == "zakon-o-zastiti-zivotne-sredine_clan_1"]
        assert len(found) == 1
        assert found[0]["text"].startswith("Član 1.")
        assert found[0]["metadata"]["chapter"] == "I. Osnovne odredbe"
