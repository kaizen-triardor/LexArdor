#!/usr/bin/env python3
"""End-to-end automated test suite for LexArdor.

Tests: ChromaDB integrity, doc_type filters, API endpoints, RAG pipeline, search quality.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.e2e_test              # Run all tests (no LLM needed)
    python -m scripts.e2e_test --with-llm   # Include LLM generation tests (needs llama-server)
"""
import json
import sys
import time
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = 0
FAIL = 0
SKIP = 0
API_BASE = "http://localhost:8080"


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name} — {reason}")


# ── ChromaDB Tests ──────────────────────────────────────────────────────────

def test_chromadb():
    print("\n=== ChromaDB Integrity ===")
    import chromadb
    c = chromadb.PersistentClient(path="data/chroma")
    col = c.get_collection("serbian_laws")
    count = col.count()
    test("Collection exists", count > 0, f"count={count}")
    test("Has >100K documents", count > 100000, f"count={count}")

    # Test doc_type distribution
    types = {}
    for offset in range(0, min(count, 200000), 50000):
        try:
            batch = col.get(limit=2000, offset=offset, include=["metadatas"])
            for m in batch["metadatas"]:
                dt = m.get("doc_type", "MISSING")
                types[dt] = types.get(dt, 0) + 1
        except:
            break

    test("Has zakon type", types.get("zakon", 0) > 0, f"types={types}")
    test("Has pravilnik type", types.get("pravilnik", 0) > 0)
    test("Has sudska_praksa type", types.get("sudska_praksa", 0) > 0)
    test("Has bilten type", types.get("bilten", 0) > 0)
    test("No MISSING doc_type > 10%", types.get("MISSING", 0) < sum(types.values()) * 0.1,
         f"MISSING={types.get('MISSING', 0)}/{sum(types.values())}")

    # Test vector search works
    from rag.embedder import embed_query
    q = embed_query("zakon o radu")
    try:
        r = col.query(query_embeddings=[q], n_results=3, include=["metadatas"])
        test("Vector search works", len(r["ids"][0]) > 0)
    except Exception as e:
        test("Vector search works", False, str(e))

    # Test filtered search
    try:
        r = col.query(query_embeddings=[q], n_results=3,
                       where={"doc_type": "zakon"},
                       include=["metadatas"])
        test("Filtered search (zakon) works", len(r["ids"][0]) > 0)
    except Exception as e:
        test("Filtered search (zakon) works", False, str(e))

    try:
        r = col.query(query_embeddings=[q], n_results=3,
                       where={"doc_type": "sudska_praksa"},
                       include=["metadatas"])
        test("Filtered search (sudska_praksa) works", len(r["ids"][0]) > 0)
    except Exception as e:
        test("Filtered search (sudska_praksa) works", False, str(e))

    print(f"  ChromaDB: {count:,} docs, types: {dict(sorted(types.items(), key=lambda x: -x[1]))}")
    return count


# ── SQLite Tests ────────────────────────────────────────────────────────────

def test_sqlite():
    print("\n=== SQLite Database ===")
    import sqlite3
    db = sqlite3.connect("data/lexardor.db")
    cur = db.cursor()

    docs = cur.execute("SELECT COUNT(*) FROM legal_documents").fetchone()[0]
    arts = cur.execute("SELECT COUNT(*) FROM legal_articles").fetchone()[0]
    refs = cur.execute("SELECT COUNT(*) FROM citation_edges").fetchone()[0]
    vers = cur.execute("SELECT COUNT(*) FROM legal_versions").fetchone()[0]

    test("Has legal_documents", docs > 1000, f"count={docs}")
    test("Has legal_articles", arts > 50000, f"count={arts}")
    test("Has citation_edges", refs > 10000, f"count={refs}")
    test("Has legal_versions", vers > 1000, f"count={vers}")

    # Check doc_type distribution
    type_dist = cur.execute(
        "SELECT doc_type, COUNT(*) FROM legal_documents GROUP BY doc_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    test("Has multiple doc_types", len(type_dist) > 3, f"types={type_dist}")

    db.close()


# ── API Tests ───────────────────────────────────────────────────────────────

def test_api():
    print("\n=== API Endpoints ===")

    try:
        r = requests.get(f"{API_BASE}/api/health", timeout=10)
        test("Health endpoint", r.status_code == 200)
        data = r.json()
        test("Health reports corpus stats", data.get("corpus_stats", {}).get("total_articles", 0) > 0,
             f"stats={data.get('corpus_stats')}")
    except Exception as e:
        test("Health endpoint", False, f"Server not running? {e}")
        print("  Skipping remaining API tests (server not available)")
        return

    # Corpus endpoints
    r = requests.get(f"{API_BASE}/api/corpus/summary", timeout=30)
    test("Corpus summary", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        test("Corpus has documents", data.get("total_documents", 0) > 1000)
        test("Corpus has by_type breakdown", len(data.get("by_type", [])) > 3)

    r = requests.get(f"{API_BASE}/api/corpus/laws", timeout=30)
    test("Corpus laws list", r.status_code == 200)
    if r.status_code == 200:
        laws = r.json()
        test("Laws list >1000", len(laws) > 1000, f"count={len(laws)}")

    # BM25 search
    r = requests.get(f"{API_BASE}/api/corpus/search?q=zakon+o+radu&top_k=5", timeout=60)
    test("BM25 search works", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        results = data.get("results", [])
        test("BM25 returns results", len(results) > 0, f"count={len(results)}")
        if results:
            test("BM25 results have doc_type", "doc_type" in results[0])


# ── RAG Pipeline Tests ─────────────────────────────────────────────────────

def test_rag():
    print("\n=== RAG Pipeline ===")
    from rag.store import search, search_with_filters
    from rag.reranker import rerank

    # Basic search
    results = search("zakon o radu prava zaposlenih", top_k=5)
    test("Basic search returns results", len(results) > 0, f"count={len(results)}")
    if results:
        test("Results have score", "score" in results[0])
        test("Results have metadata", "metadata" in results[0])

    # Filtered search - laws only
    results = search_with_filters("rokovi za žalbu", top_k=5, fetch_k=20,
                                   doc_types=["zakon", "zakonik"])
    test("Filtered search (zakoni)", len(results) > 0)
    if results:
        meta = results[0].get("metadata", {})
        test("Filter returns correct type",
             meta.get("doc_type") in ["zakon", "zakonik", ""],
             f"got={meta.get('doc_type')}")

    # Filtered search - sudska_praksa only
    results = search_with_filters("naknada štete", top_k=5, fetch_k=20,
                                   doc_types=["sudska_praksa"])
    test("Filtered search (sudska_praksa)", len(results) > 0)

    # Reranker
    candidates = search("obligaciono pravo", top_k=10)
    if len(candidates) >= 3:
        reranked = rerank("obligaciono pravo", candidates, top_k=5)
        test("Reranker works", len(reranked) > 0)
    else:
        skip("Reranker", "not enough candidates")


# ── LLM Tests (optional) ───────────────────────────────────────────────────

def test_llm():
    print("\n=== LLM Integration ===")
    try:
        r = requests.get("http://localhost:8081/health", timeout=5)
        if r.status_code != 200:
            skip("LLM test", "llama-server not running")
            return
    except:
        skip("LLM test", "llama-server not reachable")
        return

    # Test full query
    try:
        r = requests.post(f"{API_BASE}/api/query",
                          json={"query": "Šta je zakon o radu?", "top_k": 3},
                          timeout=120)
        test("Full RAG+LLM query", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            test("LLM returns answer", len(data.get("answer", "")) > 50)
            test("LLM returns sources", len(data.get("sources", [])) > 0)
            test("LLM returns citations", data.get("citations") is not None)
            print(f"  Answer preview: {data.get('answer', '')[:200]}...")
            print(f"  Response time: {data.get('response_time_ms', '?')}ms")
    except Exception as e:
        test("Full RAG+LLM query", False, str(e))

    # Test with doc_type filter
    try:
        r = requests.post(f"{API_BASE}/api/query",
                          json={"query": "naknada štete", "top_k": 3,
                                "doc_types": ["sudska_praksa"]},
                          timeout=120)
        test("Query with doc_type filter", r.status_code == 200)
    except Exception as e:
        test("Query with doc_type filter", False, str(e))


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  LexArdor v2 — End-to-End Test Suite")
    print("=" * 60)

    with_llm = "--with-llm" in sys.argv

    test_chromadb()
    test_sqlite()
    test_rag()
    test_api()
    if with_llm:
        test_llm()
    else:
        print("\n=== LLM Integration === (skipped, use --with-llm)")

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    print(f"{'=' * 60}")
    sys.exit(1 if FAIL > 0 else 0)
