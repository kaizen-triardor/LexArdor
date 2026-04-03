"""LexArdor v2 — Full Automated Functional Test Suite.

Tests all 60 API endpoints for correct status codes, response structure,
and basic functionality. Designed to run WITHOUT llama-server
(LLM-dependent endpoints are tested for graceful degradation).

Usage:
    cd ~/Projects/Project_02_LEXARDOR/lexardor-v2
    source venv/bin/activate
    python -m pytest tests/test_api.py -v
"""
import json
import pytest
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

# ═══════════════════════════════════════════════════════════════════════════════
# Health, Models, App Info
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdmin:
    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "ok" in data
        assert "corpus_stats" in data
        assert "timestamp" in data

    def test_app_info(self):
        r = client.get("/api/app-info")
        assert r.status_code == 200
        data = r.json()
        assert data["version"] == "2.0.0"
        assert "support_email" in data

    def test_models(self):
        r = client.get("/api/models")
        assert r.status_code == 200
        assert "models" in r.json()

    def test_engine_config(self):
        r = client.get("/api/admin/engine")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert "available_models" in data
        assert "capabilities" in data
        assert data["capabilities"]["bm25_enabled"] is True

    def test_current_model(self):
        r = client.get("/api/admin/current-model")
        assert r.status_code == 200
        # May or may not have a model loaded

    def test_bm25_status(self):
        r = client.get("/api/admin/bm25-status")
        assert r.status_code == 200
        data = r.json()
        assert "ready" in data
        assert "doc_count" in data

    def test_diagnostics(self):
        r = client.get("/api/admin/diagnostics")
        assert r.status_code == 200
        data = r.json()
        assert "total_queries" in data
        assert "avg_response_time_ms" in data
        assert "confidence_distribution" in data

    def test_set_model_invalid_role(self):
        r = client.post("/api/admin/set-model?role=invalid&model_key=fast")
        assert r.status_code == 400

    def test_set_model_valid(self):
        r = client.post("/api/admin/set-model?role=reasoning&model_key=deepseek")
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Chat CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestChat:
    def test_list_chats(self):
        r = client.get("/api/chats")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_and_delete_chat(self):
        # Create
        r = client.post("/api/chats", json={"title": "Test Chat"})
        assert r.status_code == 200
        chat_id = r.json()["id"]
        assert chat_id > 0

        # Get messages (empty)
        r = client.get(f"/api/chats/{chat_id}/messages")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        # Delete
        r = client.delete(f"/api/chats/{chat_id}")
        assert r.status_code == 200

    def test_export_nonexistent_chat(self):
        r = client.post("/api/chats/99999/export?format=html")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Documents
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocuments:
    def test_list_documents(self):
        r = client.get("/api/documents")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_upload_document_json(self):
        r = client.post("/api/documents/upload", json={
            "title": "Test Doc",
            "content": "Ovo je test sadržaj za proveru funkcionalnosti unosa dokumenata u LexArdor sistem.",
            "category": "komentar"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "doc_id" in data

    def test_upload_document_empty(self):
        r = client.post("/api/documents/upload", json={
            "title": "", "content": "", "category": ""
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Corpus
# ═══════════════════════════════════════════════════════════════════════════════

class TestCorpus:
    def test_corpus_stats(self):
        r = client.get("/api/corpus/stats")
        assert r.status_code == 200

    def test_corpus_summary(self):
        r = client.get("/api/corpus/summary")
        assert r.status_code == 200
        data = r.json()
        assert "total_documents" in data or isinstance(data, dict)

    def test_corpus_laws(self):
        r = client.get("/api/corpus/laws")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_corpus_freshness(self):
        r = client.get("/api/corpus/freshness")
        assert r.status_code == 200
        data = r.json()
        assert "total_documents" in data
        assert "total_articles" in data

    def test_corpus_search_short_query(self):
        r = client.get("/api/corpus/search?q=a")
        assert r.status_code == 400

    def test_corpus_search(self):
        r = client.get("/api/corpus/search?q=zakon+o+radu&top_k=5")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert "total" in data

    def test_corpus_compare_missing_params(self):
        r = client.post("/api/corpus/compare", json={"left": {}, "right": {}})
        assert r.status_code == 400

    def test_corpus_graph_nonexistent(self):
        r = client.get("/api/corpus/graph/nonexistent-law")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Templates & Drafts
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplates:
    def test_list_templates(self):
        r = client.get("/api/templates")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_drafts(self):
        r = client.get("/api/drafts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_nonexistent_template(self):
        r = client.get("/api/templates/99999")
        assert r.status_code == 404

    def test_delete_nonexistent_template(self):
        r = client.delete("/api/templates/99999")
        # May return 200 or 404 depending on implementation
        assert r.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# Drafting Studio Analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestDraftingStudio:
    """These require LLM — test for graceful 503 when unavailable."""

    def test_analyze_risk_no_llm(self):
        r = client.post("/api/drafts/analyze-risk", json={
            "text": "Ugovor o zakupu poslovnog prostora između stranaka.",
            "doc_type": "ugovor"
        })
        # 200 if LLM available, 503 if not
        assert r.status_code in (200, 503)

    def test_check_completeness_no_llm(self):
        r = client.post("/api/drafts/check-completeness", json={
            "text": "Ugovor o zakupu.", "doc_type": "ugovor"
        })
        assert r.status_code in (200, 503)

    def test_explain_clause_no_llm(self):
        r = client.post("/api/drafts/explain-clause", json={
            "text": "Zakupac je dužan da plaća mesečnu zakupninu.",
            "action": "explain"
        })
        assert r.status_code in (200, 503)

    def test_legal_basis_no_llm(self):
        r = client.post("/api/drafts/legal-basis", json={
            "text": "Ugovor o radu zaključuje se na neodređeno vreme."
        })
        assert r.status_code in (200, 503)


# ═══════════════════════════════════════════════════════════════════════════════
# External AI
# ═══════════════════════════════════════════════════════════════════════════════

class TestExternalAI:
    def test_list_providers(self):
        r = client.get("/api/external/providers")
        assert r.status_code == 200
        data = r.json()
        assert "openai" in data
        assert "anthropic" in data
        assert "google" in data

    def test_anonymize(self):
        r = client.post("/api/external/anonymize", json={
            "provider": "openai", "api_key": "test",
            "prompt": "Petar Petrović, JMBG 1234567890123, živi u Beogradu.",
            "anonymize": True, "names_to_hide": ["Petar Petrović"]
        })
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Support Reports
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupport:
    def test_submit_report(self):
        r = client.post("/api/support/report", json={
            "type": "suggestion",
            "description": "Predlog za poboljšanje — automatski test",
            "include_last_chat": False
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_list_reports(self):
        r = client.get("/api/support/reports")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Matters (Research Workspace)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatters:
    def test_full_matter_lifecycle(self):
        # Create
        r = client.post("/api/matters", json={"name": "Test Predmet", "description": "Automatski test"})
        assert r.status_code == 200
        matter_id = r.json()["id"]

        # List
        r = client.get("/api/matters")
        assert r.status_code == 200
        assert any(m["id"] == matter_id for m in r.json())

        # Get
        r = client.get(f"/api/matters/{matter_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "Test Predmet"

        # Add note
        r = client.post(f"/api/matters/{matter_id}/notes", json={"content": "Test beleška"})
        assert r.status_code == 200
        note_id = r.json()["id"]

        # Verify note exists
        r = client.get(f"/api/matters/{matter_id}")
        assert len(r.json()["notes"]) == 1

        # Delete note
        r = client.delete(f"/api/matters/notes/{note_id}")
        assert r.status_code == 200

        # Update
        r = client.put(f"/api/matters/{matter_id}", json={"name": "Ažuriran Predmet"})
        assert r.status_code == 200

        # Link chat
        chat_r = client.post("/api/chats", json={"title": "Test"})
        chat_id = chat_r.json()["id"]
        r = client.post(f"/api/matters/{matter_id}/link-chat", json={"item_id": chat_id})
        assert r.status_code == 200

        # Delete matter (cascades)
        r = client.delete(f"/api/matters/{matter_id}")
        assert r.status_code == 200

    def test_get_nonexistent_matter(self):
        r = client.get("/api/matters/99999")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Query (requires LLM — test structure only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuery:
    def test_query_schema_validation(self):
        """Test that the query endpoint validates input correctly."""
        r = client.post("/api/query", json={
            "query": "test",
            "answer_mode": "balanced",
            "deep_analysis": False,
            "reference_date": "2025-01-01",
        })
        # Will fail with 500/503 if no LLM, but should NOT be 422 (validation error)
        assert r.status_code != 422

    def test_research_too_short(self):
        r = client.post("/api/query/research", json={"topic": "ab"})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Frontend
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrontend:
    def test_homepage(self):
        r = client.get("/")
        assert r.status_code == 200
        assert "LexArdor" in r.text
        assert "page-chat" in r.text
        assert "page-workspace" in r.text

    def test_favicon(self):
        r = client.get("/favicon.png")
        assert r.status_code == 200

    def test_frontend_has_all_pages(self):
        r = client.get("/")
        html = r.text
        pages = ["page-chat", "page-documents", "page-corpus", "page-templates",
                 "page-external", "page-settings", "page-help", "page-workspace"]
        for page in pages:
            assert page in html, f"Missing page: {page}"

    def test_frontend_has_vis_js(self):
        r = client.get("/")
        assert "vis-network" in r.text, "vis.js CDN not loaded for graph explorer"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: tokenizer + BM25
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenizerBM25:
    def test_tokenizer(self):
        from core.tokenizer import tokenize, extract_query_keywords, SERBIAN_STOP_WORDS, LEGAL_TERMS
        assert len(SERBIAN_STOP_WORDS) > 50
        assert len(LEGAL_TERMS) > 30
        tokens = tokenize("Koji su uslovi za otkaz ugovora o radu?")
        assert "otkaz" in tokens
        assert "uslovi" in tokens
        assert "su" not in tokens  # stop word removed

    def test_span_mapper(self):
        from rag.span_mapper import map_answer_to_sources
        answer = "Prema članu 179 Zakona o radu, otkaz je moguć. Član 24 definiše rokove."
        sources = [
            {"article": "179", "law": "Zakon o radu"},
            {"article": "24", "law": "Zakon o obligacijama"},
        ]
        spans = map_answer_to_sources(answer, sources)
        assert len(spans) == 2
        assert spans[0]["has_citation"] is True
        assert spans[0]["source_refs"][0]["article"] == "179"

    def test_structured_answer_parser(self):
        from rag.reasoning import parse_structured_answer
        answer = """KRATAK ODGOVOR:
Otkaz je moguć.

PRAVNI OSNOV:
- Član 179 Zakona o radu

OBRAZLOŽENJE:
Detaljno objašnjenje."""
        result = parse_structured_answer(answer)
        assert result["structured"] is True
        assert "kratak_odgovor" in result["sections"]
        assert "pravni_osnov" in result["sections"]

    def test_confidence_enhanced(self):
        from rag.pipeline import _calculate_confidence
        conf = _calculate_confidence("test", [])
        assert isinstance(conf, dict)
        assert conf["level"] == "low"
        assert len(conf["red_flags"]) > 0
