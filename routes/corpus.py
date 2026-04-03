"""Corpus and law management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, Query

from core.config import settings
from rag.store import get_stats as corpus_stats, get_client_stats, get_collection, ingest_law
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["corpus"])


@router.get("/corpus/stats")
def corpus_stats_endpoint():
    try:
        core = corpus_stats()
    except Exception:
        core = {"total_articles": 0}
    try:
        client = get_client_stats()
    except Exception:
        client = {"total_documents": 0, "total_chunks": 0}
    return {**core, "client_documents": client}


@router.post("/corpus/upload-law")
async def upload_law(
    file: UploadFile = File(None),
    text: str = Form(None),
    title: str = Form(...),
    gazette: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """Upload a law (PDF/DOCX/TXT) to the core legal database.
    Parses into articles (Clan) and ingests into the main corpus.
    """
    from scraper.parser import parse_law_text, slugify
    from core.doc_extractor import extract_text as extract_doc_text
    import json as _json
    from pathlib import Path

    # Extract text
    if file and file.filename:
        file_bytes = await file.read()
        try:
            raw_text = extract_doc_text(file_bytes, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif text and text.strip():
        raw_text = text.strip()
    else:
        raise HTTPException(status_code=400, detail="Priložite dokument ili unesite tekst zakona.")

    if len(raw_text) < 100:
        raise HTTPException(status_code=400, detail="Dokument je prekratak. Očekuje se pun tekst zakona.")

    # Parse into structured law with articles
    slug = slugify(title)
    law = parse_law_text(raw_text)
    law["slug"] = slug  # Override with user-provided title slug
    law["title"] = title  # Override with user-provided title
    if gazette:
        law["gazette"] = gazette
    law["source_url"] = "user_upload"

    if not law["articles"]:
        raise HTTPException(
            status_code=400,
            detail=f"Nije pronađen nijedan član (Član) u dokumentu. "
                   f"Proverite da dokument sadrži strukturu sa 'Član 1.', 'Član 2.' itd."
        )

    # Save JSON to data/laws/ for persistence
    laws_dir = Path(settings.laws_path)
    laws_dir.mkdir(parents=True, exist_ok=True)
    json_path = laws_dir / f"{slug}.json"
    json_path.write_text(_json.dumps(law, ensure_ascii=False, indent=2), encoding="utf-8")

    # Ingest into ChromaDB core collection
    count = ingest_law(law)

    return {
        "ok": True,
        "slug": slug,
        "title": title,
        "articles_found": len(law["articles"]),
        "articles_ingested": count,
        "gazette": law.get("gazette", ""),
        "saved_to": str(json_path),
    }


@router.get("/corpus/laws")
def list_corpus_laws():
    """List all laws in the corpus from SQLite legal_documents table."""
    from db.legal_schema import get_legal_db
    try:
        conn = get_legal_db()
        rows = conn.execute(
            "SELECT slug, title, doc_type, gazette_ref, article_count FROM legal_documents ORDER BY slug"
        ).fetchall()
        conn.close()

        def readable_title(slug, title, gazette):
            """Generate readable title: prefer DB title, fallback to slug-derived name."""
            # If title exists and is not just a gazette reference
            if title and not title.startswith('(') and not title.startswith('"') and not title.startswith('-') and len(title) > 3:
                # Check it's not just a gazette ref
                if 'GLASNIK' not in title.upper() and 'glasnik' not in title.lower():
                    return title
            # Generate from slug
            name = slug.replace('-', ' ').replace('_', ' ')
            # Remove year suffix like "-2020", "-2019"
            import re
            name = re.sub(r'\s*\d{4}\s*$', '', name)
            # Capitalize properly for Serbian legal naming
            name = name.strip().capitalize()
            # Capitalize after "o ", "i ", "za "
            for prep in [' o ', ' i ', ' za ', ' u ', ' na ', ' od ', ' sa ', ' po ', ' iz ']:
                name = name.replace(prep, prep)
            return name if name else slug

        return [{
            "slug": r["slug"],
            "title": readable_title(r["slug"], r["title"], r["gazette_ref"]),
            "doc_type": r["doc_type"] or "",
            "gazette": r["gazette_ref"] or "",
            "article_count": r["article_count"] or 0,
        } for r in rows]
    except Exception:
        # Fallback to JSON files
        from pathlib import Path
        import json as _json
        laws_dir = Path(settings.laws_path)
        if not laws_dir.exists():
            return []
        laws = []
        for f in sorted(laws_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                laws.append({
                    "slug": data.get("slug", f.stem),
                    "title": data.get("title", f.stem),
                    "doc_type": data.get("doc_type", ""),
                    "gazette": data.get("gazette", ""),
                    "article_count": len(data.get("articles", [])),
                })
            except Exception:
                continue
        return laws


@router.delete("/corpus/laws/{slug}")
def delete_corpus_law(slug: str, user: dict = Depends(get_current_user)):
    """Delete a law from the corpus (only user-uploaded laws)."""
    from pathlib import Path
    import json as _json

    laws_dir = Path(settings.laws_path)
    json_path = laws_dir / f"{slug}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Zakon nije pronađen")

    # Check if it was user-uploaded
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    if data.get("source_url") != "user_upload":
        raise HTTPException(status_code=403, detail="Možete brisati samo zakone koje ste vi dodali")

    # Delete from ChromaDB
    collection = get_collection()
    # Get all article IDs for this law
    existing = collection.get(where={"law_slug": slug})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    # Delete JSON file
    json_path.unlink()

    return {"ok": True, "deleted_articles": len(existing["ids"])}


# ── Structured Legal Data endpoints (Sprint 1) ──────────────────────────────

@router.get("/corpus/freshness")
def corpus_freshness_endpoint():
    """Corpus freshness indicator — last update date and document/article totals."""
    from db.legal_schema import get_legal_db
    conn = get_legal_db()
    row = conn.execute("""
        SELECT
            COALESCE(MAX(COALESCE(scraped_at, created_at)), '') AS last_updated,
            COUNT(*) AS total_documents
        FROM legal_documents
    """).fetchone()
    total_articles = conn.execute(
        "SELECT COUNT(*) AS c FROM legal_articles"
    ).fetchone()["c"]
    conn.close()
    return {
        "last_updated": row["last_updated"] if row["last_updated"] else None,
        "total_documents": row["total_documents"],
        "total_articles": total_articles,
    }


@router.get("/corpus/summary")
def corpus_summary_endpoint():
    """Rich structured corpus statistics from SQLite legal schema."""
    from db.legal_schema import get_corpus_summary
    return get_corpus_summary()


@router.get("/corpus/laws/{slug}/articles")
def get_law_articles(slug: str):
    """List all articles for a law with sub-article counts, grouped by chapter."""
    from db.legal_schema import get_document_by_slug, get_document_articles
    doc = get_document_by_slug(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Zakon nije pronađen")
    articles = get_document_articles(slug)

    # Group articles by chapter for chapter-based navigation
    chapters_map: dict[str, list[dict]] = {}
    for art in articles:
        ch = art.get("chapter") or ""
        chapters_map.setdefault(ch, []).append(art)

    chapters = [
        {"name": ch_name, "articles": ch_articles}
        for ch_name, ch_articles in chapters_map.items()
    ]

    return {
        "document": doc,
        "articles": articles,       # flat list — backward compat
        "chapters": chapters,       # grouped by chapter
    }


@router.get("/corpus/laws/{slug}/articles/{article_number}")
def get_law_article_detail(slug: str, article_number: str):
    """Get a single article with full sub-structure and cross-references."""
    from db.legal_schema import get_article_detail, get_inbound_references
    article = get_article_detail(slug, article_number)
    if not article:
        raise HTTPException(status_code=404, detail="Član nije pronađen")
    # Also get inbound references (who cites this article)
    inbound = get_inbound_references(slug, article_number)
    article["inbound_references"] = inbound
    return article


@router.get("/corpus/laws/{slug}/versions")
def get_law_versions(slug: str):
    """Get gazette amendment history for a law."""
    from db.legal_schema import get_document_by_slug, get_document_versions
    doc = get_document_by_slug(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Zakon nije pronađen")
    versions = get_document_versions(slug)
    return {"document": doc, "versions": versions}


@router.post("/corpus/explain")
def explain_article(body: dict, user: dict = Depends(get_current_user)):
    """AI explanation of a legal article in simple language."""
    from llm.ollama import OllamaClient

    article_text = body.get("text", "")
    law_name = body.get("law", "")
    article_num = body.get("article", "")
    mode = body.get("mode", "citizen")  # citizen or expert

    if not article_text:
        raise HTTPException(status_code=400, detail="Article text required")

    if mode == "citizen":
        system = """Objasni ovaj član zakona JEDNOSTAVNIM jezikom, kao da objašnjavaš prijatelju koji nije pravnik.
Koristi kratke rečenice i primere iz svakodnevnog života. Maksimalno 3-4 rečenice."""
    else:
        system = """Daj stručni komentar ovog člana zakona. Navedi:
1. Suštinu odredbe (1-2 rečenice)
2. Praktične implikacije za advokate
3. Povezane propise ako su relevantni
Budi koncizan — maksimalno 5-6 rečenica."""

    prompt = f"""Zakon: {law_name}
Član {article_num}:
{article_text}"""

    client = OllamaClient()
    if not client.is_available():
        raise HTTPException(status_code=503, detail="AI model nije dostupan")
    explanation = client.generate(prompt, system=system, max_tokens=500)
    return {"explanation": explanation, "mode": mode}


@router.post("/corpus/compare")
def compare_articles(body: dict, user: dict = Depends(get_current_user)):
    """Compare two legal articles side by side with diff highlighting."""
    import difflib
    from db.legal_schema import get_article_detail

    left = body.get("left", {})
    right = body.get("right", {})

    if not left.get("slug") or not left.get("article"):
        raise HTTPException(status_code=400, detail="Left article required (slug + article)")
    if not right.get("slug") or not right.get("article"):
        raise HTTPException(status_code=400, detail="Right article required (slug + article)")

    left_art = get_article_detail(left["slug"], left["article"])
    right_art = get_article_detail(right["slug"], right["article"])

    if not left_art:
        raise HTTPException(status_code=404, detail=f"Left article not found: {left['slug']} Član {left['article']}")
    if not right_art:
        raise HTTPException(status_code=404, detail=f"Right article not found: {right['slug']} Član {right['article']}")

    # Compute diff
    left_lines = (left_art.get("full_text", "") or "").splitlines()
    right_lines = (right_art.get("full_text", "") or "").splitlines()
    diff = list(difflib.unified_diff(left_lines, right_lines,
                                      fromfile=f"{left['slug']} Član {left['article']}",
                                      tofile=f"{right['slug']} Član {right['article']}",
                                      lineterm=""))

    return {
        "left": left_art,
        "right": right_art,
        "diff": diff,
        "has_differences": len(diff) > 0,
    }


@router.get("/corpus/search")
def search_corpus_articles(q: str, top_k: int = 20,
                            user: dict = Depends(get_current_user)):
    """Full-text search across article content using BM25 index."""
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Search query too short")

    from rag.bm25 import bm25_search, get_bm25_index, build_bm25_index
    idx = get_bm25_index()
    if not idx.ready:
        # Try loading from disk; if not available, start background build
        build_bm25_index(background=True)
        if not idx.ready:
            return {"results": [], "total": 0, "status": "bm25_building",
                    "message": "BM25 indeks se gradi u pozadini. Pokušajte ponovo za minut."}

    results = bm25_search(q, top_k=top_k)
    if not results:
        return {"results": [], "total": 0}

    # Fetch article details from ChromaDB
    collection = get_collection()
    doc_ids = [r[0] for r in results]
    scores = {r[0]: r[1] for r in results}

    fetched = collection.get(ids=doc_ids, include=["documents", "metadatas"])

    articles = []
    for i, doc_id in enumerate(fetched["ids"]):
        meta = fetched["metadatas"][i]
        text = fetched["documents"][i]
        articles.append({
            "id": doc_id,
            "slug": meta.get("law_slug", ""),
            "title": meta.get("law_title", ""),
            "article_number": meta.get("article_number", ""),
            "text_preview": text[:200] + "..." if len(text) > 200 else text,
            "doc_type": meta.get("doc_type", ""),
            "bm25_score": round(scores.get(doc_id, 0), 3),
        })

    return {"results": articles, "total": len(articles)}


@router.get("/corpus/graph/{slug}")
def get_citation_graph(slug: str, depth: int = Query(1, ge=1, le=3),
                       user: dict = Depends(get_current_user)):
    """Get citation graph data for a law -- nodes (articles) and edges (citations).

    Returns data formatted for vis.js Network visualization.
    Depth controls how many hops of cross-references to follow (1-3).
    """
    from db.legal_schema import (
        get_document_by_slug, get_document_articles, get_legal_db,
    )

    doc = get_document_by_slug(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Zakon nije pronađen")

    conn = get_legal_db()

    # Collect all articles for this law as the seed set
    articles = get_document_articles(slug)
    if not articles:
        conn.close()
        return {"nodes": [], "edges": [], "stats": {"total_nodes": 0, "total_edges": 0, "laws_involved": 0}}

    # Build node and edge sets iteratively by depth
    nodes: dict[str, dict] = {}   # keyed by node_id
    edges: list[dict] = []
    edge_set: set[tuple[str, str]] = set()  # dedup edges
    laws_involved: set[str] = set()

    # Seed: all articles of this law
    article_ids_to_expand: list[int] = []
    for art in articles:
        node_id = f"{slug}_clan_{art['article_number']}"
        nodes[node_id] = {
            "id": node_id,
            "label": f"\u010cl. {art['article_number']}",
            "group": slug,
            "title": art.get("full_text", "")[:200] + ("..." if len(art.get("full_text", "")) > 200 else ""),
        }
        article_ids_to_expand.append(art["id"])
        laws_involved.add(slug)

    # Expand by depth
    for current_depth in range(depth):
        next_article_ids: list[int] = []

        if not article_ids_to_expand:
            break

        # Batch fetch outgoing edges for current set
        placeholders = ",".join("?" * len(article_ids_to_expand))
        outgoing = conn.execute(f"""
            SELECT ce.source_article_id, ce.target_document_slug,
                   ce.target_article_number, ce.citation_text,
                   a.article_number AS source_article_number,
                   d.slug AS source_slug
            FROM citation_edges ce
            JOIN legal_articles a ON ce.source_article_id = a.id
            JOIN legal_documents d ON a.document_id = d.id
            WHERE ce.source_article_id IN ({placeholders})
        """, article_ids_to_expand).fetchall()

        for row in outgoing:
            source_slug = row["source_slug"]
            source_art = row["source_article_number"]
            target_slug = row["target_document_slug"]
            target_art = row["target_article_number"]

            if not target_slug or not target_art:
                continue

            source_id = f"{source_slug}_clan_{source_art}"
            target_id = f"{target_slug}_clan_{target_art}"

            # Add target node if not seen
            if target_id not in nodes:
                # Try to get preview text
                target_row = conn.execute("""
                    SELECT a.id, a.full_text FROM legal_articles a
                    JOIN legal_documents d ON a.document_id = d.id
                    WHERE d.slug = ? AND a.article_number = ?
                """, (target_slug, target_art)).fetchone()
                preview = ""
                if target_row:
                    preview = target_row["full_text"][:200] + ("..." if len(target_row["full_text"]) > 200 else "")
                    next_article_ids.append(target_row["id"])

                nodes[target_id] = {
                    "id": target_id,
                    "label": f"\u010cl. {target_art}",
                    "group": target_slug,
                    "title": preview,
                }
                laws_involved.add(target_slug)

            # Add edge if not duplicate
            edge_key = (source_id, target_id)
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                edges.append({
                    "from": source_id,
                    "to": target_id,
                    "label": row["citation_text"][:30] if row["citation_text"] else "poziva se na",
                })

        # Also fetch incoming edges to seed articles (articles that cite us)
        incoming = conn.execute(f"""
            SELECT ce.source_article_id, ce.target_document_slug,
                   ce.target_article_number, ce.citation_text,
                   a.article_number AS source_article_number,
                   d.slug AS source_slug
            FROM citation_edges ce
            JOIN legal_articles a ON ce.source_article_id = a.id
            JOIN legal_documents d ON a.document_id = d.id
            WHERE ce.target_document_slug = ?
              AND ce.target_article_number IN (
                  SELECT la.article_number FROM legal_articles la
                  JOIN legal_documents ld ON la.document_id = ld.id
                  WHERE ld.slug = ? AND la.id IN ({placeholders})
              )
        """, [slug, slug] + article_ids_to_expand).fetchall()

        for row in incoming:
            source_slug = row["source_slug"]
            source_art = row["source_article_number"]
            target_slug = row["target_document_slug"]
            target_art = row["target_article_number"]

            source_id = f"{source_slug}_clan_{source_art}"
            target_id = f"{target_slug}_clan_{target_art}"

            # Add source node if not seen (external article citing us)
            if source_id not in nodes:
                source_row = conn.execute("""
                    SELECT a.id, a.full_text FROM legal_articles a
                    JOIN legal_documents d ON a.document_id = d.id
                    WHERE d.slug = ? AND a.article_number = ?
                """, (source_slug, source_art)).fetchone()
                preview = ""
                if source_row:
                    preview = source_row["full_text"][:200] + ("..." if len(source_row["full_text"]) > 200 else "")
                    next_article_ids.append(source_row["id"])

                nodes[source_id] = {
                    "id": source_id,
                    "label": f"\u010cl. {source_art}",
                    "group": source_slug,
                    "title": preview,
                }
                laws_involved.add(source_slug)

            edge_key = (source_id, target_id)
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                edges.append({
                    "from": source_id,
                    "to": target_id,
                    "label": row["citation_text"][:30] if row["citation_text"] else "poziva se na",
                })

        # Next depth expands newly discovered article IDs
        article_ids_to_expand = next_article_ids

    conn.close()

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "laws_involved": len(laws_involved),
        },
    }
