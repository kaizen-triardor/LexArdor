"""LexArdor v2 — LIVE Functional Validation Suite.

Runs against live server (localhost:8080 + llama-server on :8081).
Tests every endpoint including LLM-dependent ones.

Usage:
    python tests/test_live_validation.py
"""
import json
import time
import sys
import requests

BASE = "http://localhost:8080"
RESULTS = []
TOTAL = 0
PASSED = 0
FAILED = 0


def test(name, method, path, **kwargs):
    global TOTAL, PASSED, FAILED
    TOTAL += 1
    expected = kwargs.pop("expected_status", 200)
    check_fn = kwargs.pop("check", None)
    timeout = kwargs.pop("timeout", 30)

    try:
        url = f"{BASE}{path}"
        if method == "GET":
            r = requests.get(url, timeout=timeout, params=kwargs.get("params"))
        elif method == "POST":
            if "json_body" in kwargs:
                r = requests.post(url, json=kwargs["json_body"], timeout=timeout)
            elif "data" in kwargs:
                r = requests.post(url, data=kwargs["data"], files=kwargs.get("files"), timeout=timeout)
            else:
                r = requests.post(url, timeout=timeout)
        elif method == "PUT":
            r = requests.put(url, json=kwargs.get("json_body", {}), timeout=timeout)
        elif method == "DELETE":
            r = requests.delete(url, timeout=timeout)
        else:
            raise ValueError(f"Unknown method: {method}")

        status_ok = r.status_code == expected
        check_ok = True
        check_msg = ""

        if status_ok and check_fn:
            try:
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
                check_ok = check_fn(data)
                if not check_ok:
                    check_msg = f" [check failed on data: {str(data)[:100]}]"
            except Exception as e:
                check_ok = False
                check_msg = f" [check error: {e}]"

        if status_ok and check_ok:
            PASSED += 1
            RESULTS.append(("PASS", name, f"{r.status_code} ({r.elapsed.total_seconds():.2f}s)"))
            print(f"  ✅ {name} — {r.status_code} ({r.elapsed.total_seconds():.2f}s)")
        else:
            FAILED += 1
            reason = f"expected {expected}, got {r.status_code}{check_msg}"
            RESULTS.append(("FAIL", name, reason))
            print(f"  ❌ {name} — {reason}")
    except Exception as e:
        FAILED += 1
        RESULTS.append(("FAIL", name, str(e)))
        print(f"  ❌ {name} — {e}")


def main():
    global TOTAL, PASSED, FAILED
    print("=" * 70)
    print("  LEXARDOR v2 — LIVE FUNCTIONAL VALIDATION REPORT")
    print("  Server: localhost:8080 | LLM: localhost:8081")
    print("=" * 70)
    print()

    # ── 1. Health & Admin ─────────────────────────────────────────
    print("─── HEALTH & ADMIN ───")
    test("Health check", "GET", "/api/health",
         check=lambda d: d["ok"] is True and d["ollama_available"] is True)
    test("App info", "GET", "/api/app-info",
         check=lambda d: d["version"] == "2.0.0")
    test("List models", "GET", "/api/models",
         check=lambda d: "models" in d)
    test("Engine config", "GET", "/api/admin/engine",
         check=lambda d: d["capabilities"]["bm25_enabled"] is True)
    test("Current model", "GET", "/api/admin/current-model",
         check=lambda d: "loaded" in d)
    test("BM25 status", "GET", "/api/admin/bm25-status",
         check=lambda d: "ready" in d)
    test("Diagnostics", "GET", "/api/admin/diagnostics",
         check=lambda d: "total_queries" in d)
    test("Set model (valid)", "POST", "/api/admin/set-model?role=reasoning&model_key=deepseek",
         check=lambda d: d["ok"] is True)
    test("Set model (invalid role)", "POST", "/api/admin/set-model?role=invalid&model_key=fast",
         expected_status=400)
    print()

    # ── 2. Corpus ─────────────────────────────────────────────────
    print("─── CORPUS ───")
    test("Corpus stats", "GET", "/api/corpus/stats",
         check=lambda d: d.get("total_articles", 0) > 0)
    test("Corpus summary", "GET", "/api/corpus/summary",
         check=lambda d: d.get("total_documents", 0) > 0)
    test("Corpus freshness", "GET", "/api/corpus/freshness",
         check=lambda d: d["total_articles"] > 0)
    test("List laws", "GET", "/api/corpus/laws",
         check=lambda d: isinstance(d, list) and len(d) > 0)

    # Get first law for drill-down tests
    laws = requests.get(f"{BASE}/api/corpus/laws").json()
    if laws:
        slug = laws[0]["slug"]
        test(f"Law articles ({slug[:30]})", "GET", f"/api/corpus/laws/{slug}/articles",
             check=lambda d: "articles" in d and len(d["articles"]) > 0)

        articles = requests.get(f"{BASE}/api/corpus/laws/{slug}/articles").json()
        if articles.get("articles"):
            art_num = articles["articles"][0]["article_number"]
            test(f"Article detail (Čl. {art_num})", "GET",
                 f"/api/corpus/laws/{slug}/articles/{art_num}",
                 check=lambda d: d.get("article_number") == art_num or d.get("full_text"))

        test(f"Law versions ({slug[:30]})", "GET", f"/api/corpus/laws/{slug}/versions",
             check=lambda d: "document" in d)

        test(f"Citation graph ({slug[:30]})", "GET", f"/api/corpus/graph/{slug}",
             check=lambda d: "nodes" in d and "edges" in d and "stats" in d)

    test("Corpus search (BM25)", "GET", "/api/corpus/search",
         params={"q": "otkaz ugovora o radu", "top_k": 5},
         check=lambda d: d["total"] > 0)
    test("Corpus search short query", "GET", "/api/corpus/search",
         params={"q": "a"}, expected_status=400)
    test("Compare (missing params)", "POST", "/api/corpus/compare",
         json_body={"left": {}, "right": {}}, expected_status=400)
    test("Explain article (AI)", "POST", "/api/corpus/explain",
         json_body={"text": "Zaposleni ima pravo na godišnji odmor u trajanju od najmanje 20 radnih dana.",
                    "law": "Zakon o radu", "article": "68", "mode": "citizen"},
         timeout=120,
         check=lambda d: len(d.get("explanation", "")) > 20)
    print()

    # ── 3. Chat & Query ──────────────────────────────────────────
    print("─── CHAT & QUERY (LLM) ───")
    test("List chats", "GET", "/api/chats",
         check=lambda d: isinstance(d, list))

    # Create chat and ask a question
    chat_r = requests.post(f"{BASE}/api/chats", json={"title": "Validacija"})
    chat_id = chat_r.json()["id"]
    test("Create chat", "POST", "/api/chats",
         json_body={"title": "Test Validacija"},
         check=lambda d: d["id"] > 0)

    test("RAG query (balanced)", "POST", "/api/query",
         json_body={
             "query": "Koji je otkazni rok za zaposlenog sa 5 godina staža?",
             "answer_mode": "balanced",
             "reference_date": "2026-01-01",
         },
         timeout=180,
         check=lambda d: (
             len(d.get("answer", "")) > 50 and
             len(d.get("sources", [])) > 0 and
             "confidence" in d and
             "citations" in d and
             "chat_id" in d
         ))

    # Get the chat_id from the query for verify test
    query_result = requests.post(f"{BASE}/api/query", json={
        "query": "Da li trudnica može dobiti otkaz?",
        "answer_mode": "strict",
    }, timeout=180).json()
    test_chat_id = query_result.get("chat_id")

    if test_chat_id:
        test("Verify citations (LLM)", "POST", f"/api/query/{test_chat_id}/verify-citations",
             timeout=120,
             check=lambda d: "verified" in d or "method" in d)

        test("Export chat (HTML)", "POST", f"/api/chats/{test_chat_id}/export",
             params={"format": "html"},
             check=lambda d: "LEXARDOR" in str(d) or "LexArdor" in str(d))

    test("Research agent", "POST", "/api/query/research",
         json_body={"topic": "Prava zaposlenih pri otkazu ugovora o radu u Srbiji", "max_queries": 3},
         timeout=300,
         check=lambda d: len(d.get("report", "")) > 100 and len(d.get("sources", [])) > 0)

    test("Research too short", "POST", "/api/query/research",
         json_body={"topic": "ab"}, expected_status=400)

    test("Get chat messages", "GET", f"/api/chats/{chat_id}/messages",
         check=lambda d: isinstance(d, list))
    test("Delete chat", "DELETE", f"/api/chats/{chat_id}")
    print()

    # ── 4. Documents ──────────────────────────────────────────────
    print("─── DOCUMENTS ───")
    test("List documents", "GET", "/api/documents",
         check=lambda d: isinstance(d, list))
    test("Upload doc (JSON)", "POST", "/api/documents/upload",
         json_body={"title": "Test Komentar", "content": "Ovo je testni komentar za validaciju sistema.", "category": "komentar"},
         check=lambda d: d["ok"] is True)
    test("Upload doc (empty)", "POST", "/api/documents/upload",
         json_body={"title": "", "content": ""}, expected_status=400)
    print()

    # ── 5. Templates & Drafts ────────────────────────────────────
    print("─── TEMPLATES & DRAFTS ───")
    test("List templates", "GET", "/api/templates",
         check=lambda d: isinstance(d, list))
    test("List drafts", "GET", "/api/drafts",
         check=lambda d: isinstance(d, list))
    test("Get nonexistent template", "GET", "/api/templates/99999", expected_status=404)

    # Drafting studio analysis
    contract_text = """UGOVOR O ZAKUPU POSLOVNOG PROSTORA
Zakupodavac: Petar Petrović, JMBG 1234567890123
Zakupac: DOO "Test" Beograd, PIB 123456789
Predmet: Poslovni prostor u Beogradu, ul. Knez Mihailova 10, površine 50m2.
Zakupnina: 500 EUR mesečno.
Trajanje: Od 01.01.2026. do 31.12.2026.
"""
    test("Analyze risk (AI)", "POST", "/api/drafts/analyze-risk",
         json_body={"text": contract_text, "doc_type": "ugovor"},
         timeout=120,
         check=lambda d: "risks" in d)
    test("Check completeness (AI)", "POST", "/api/drafts/check-completeness",
         json_body={"text": contract_text, "doc_type": "ugovor"},
         timeout=120,
         check=lambda d: "missing" in d or "completeness_score" in d)
    test("Legal basis", "POST", "/api/drafts/legal-basis",
         json_body={"text": contract_text},
         timeout=120,
         check=lambda d: "suggestions" in d)
    test("Explain clause (AI)", "POST", "/api/drafts/explain-clause",
         json_body={"text": "Zakupac je dužan da plaća mesečnu zakupninu u iznosu od 500 EUR.", "action": "simplify"},
         timeout=120,
         check=lambda d: "result" in d and len(d["result"]) > 10)
    print()

    # ── 6. External AI ───────────────────────────────────────────
    print("─── EXTERNAL AI ───")
    test("List providers", "GET", "/api/external/providers",
         check=lambda d: "openai" in d and "anthropic" in d)
    test("Anonymize text", "POST", "/api/external/anonymize",
         json_body={
             "provider": "openai", "api_key": "test",
             "prompt": "Petar Petrović, JMBG 1234567890123, živi u Beogradu.",
             "anonymize": True, "names_to_hide": ["Petar Petrović"]
         },
         check=lambda d: "anonymized" in str(d).lower() or "text" in d)
    print()

    # ── 7. Support ───────────────────────────────────────────────
    print("─── SUPPORT ───")
    test("Submit report", "POST", "/api/support/report",
         json_body={"type": "bug", "description": "Automatski test — validacija", "include_last_chat": False},
         check=lambda d: d["ok"] is True)
    test("List reports", "GET", "/api/support/reports",
         check=lambda d: isinstance(d, list))
    print()

    # ── 8. Matters (Research Workspace) ──────────────────────────
    print("─── MATTERS (WORKSPACE) ───")
    # Full lifecycle
    r = requests.post(f"{BASE}/api/matters", json={"name": "Predmet Validacija", "description": "Automatski test"})
    matter_id = r.json()["id"]
    test("Create matter", "POST", "/api/matters",
         json_body={"name": "Test Predmet QA", "description": "QA validacija"},
         check=lambda d: d["id"] > 0)
    test("List matters", "GET", "/api/matters",
         check=lambda d: isinstance(d, list) and len(d) > 0)
    test("Get matter", "GET", f"/api/matters/{matter_id}",
         check=lambda d: d["name"] == "Predmet Validacija")

    # Add note
    note_r = requests.post(f"{BASE}/api/matters/{matter_id}/notes",
                           json={"content": "Beleška za validaciju"})
    note_id = note_r.json()["id"]
    test("Add note", "POST", f"/api/matters/{matter_id}/notes",
         json_body={"content": "Druga beleška"},
         check=lambda d: d["id"] > 0)
    test("Delete note", "DELETE", f"/api/matters/notes/{note_id}")
    test("Update matter", "PUT", f"/api/matters/{matter_id}",
         json_body={"name": "Ažuriran Predmet"})
    test("Delete matter", "DELETE", f"/api/matters/{matter_id}")
    test("Get nonexistent matter", "GET", "/api/matters/99999", expected_status=404)
    print()

    # ── 9. Frontend ──────────────────────────────────────────────
    print("─── FRONTEND ───")
    test("Homepage loads", "GET", "/",
         check=lambda d: "LexArdor" in str(d) and "page-chat" in str(d))
    test("Has 8 pages", "GET", "/",
         check=lambda d: all(p in str(d) for p in [
             "page-chat", "page-documents", "page-corpus", "page-templates",
             "page-external", "page-settings", "page-help", "page-workspace"
         ]))
    test("Has vis.js CDN", "GET", "/",
         check=lambda d: "vis-network" in str(d))
    test("Has i18n system", "GET", "/",
         check=lambda d: "data-i18n" in str(d) and "setLang" in str(d))
    test("Has dark mode", "GET", "/",
         check=lambda d: "data-theme" in str(d) and "toggleTheme" in str(d))
    test("Favicon", "GET", "/favicon.png")
    print()

    # ── REPORT ───────────────────────────────────────────────────
    print("=" * 70)
    print(f"  RESULTS: {PASSED}/{TOTAL} PASSED, {FAILED} FAILED")
    print(f"  Pass rate: {PASSED/TOTAL*100:.1f}%")
    print("=" * 70)

    if FAILED > 0:
        print("\n  FAILURES:")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"    ❌ {name}: {detail}")

    # Write report
    with open("tests/VALIDATION_REPORT.md", "w") as f:
        f.write("# LexArdor v2 — Functional Validation Report\n\n")
        f.write(f"**Date:** 2026-03-31\n")
        f.write(f"**Server:** localhost:8080 | LLM: Qwen 3.5 9B Q8 on localhost:8081\n")
        f.write(f"**Corpus:** 79,111 articles\n\n")
        f.write(f"## Summary: {PASSED}/{TOTAL} PASSED ({PASSED/TOTAL*100:.1f}%)\n\n")
        f.write("| Status | Test | Detail |\n|--------|------|--------|\n")
        for status, name, detail in RESULTS:
            icon = "✅" if status == "PASS" else "❌"
            f.write(f"| {icon} | {name} | {detail} |\n")

    print(f"\n  Report saved: tests/VALIDATION_REPORT.md")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
