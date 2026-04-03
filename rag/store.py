"""ChromaDB vector store for Serbian law articles and client documents."""

from __future__ import annotations

import re
import time

import chromadb

from core.config import settings
from rag.embedder import embed_query, embed_texts

_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_client_collection: chromadb.Collection | None = None


def _get_client() -> chromadb.PersistentClient:
    """Lazy-init ChromaDB PersistentClient."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_path)
    return _client


def get_collection() -> chromadb.Collection:
    """Lazy-init and return the 'core_laws' collection (backward compat)."""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name="serbian_laws",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def get_client_collection() -> chromadb.Collection:
    """Lazy-init and return the 'client_documents' collection."""
    global _client_collection
    if _client_collection is None:
        client = _get_client()
        _client_collection = client.get_or_create_collection(
            name="client_documents",
            metadata={"hnsw:space": "cosine"},
        )
    return _client_collection


def ingest_law(law: dict) -> int:
    """Ingest a parsed law dict into ChromaDB.

    Expected keys: slug, title, gazette, source_url, articles[{number, text, chapter}].
    Enhanced keys (optional): doc_type, authority_level, valid_from, stav_count, tacka_count.
    Creates one document per article with ID '{slug}_clan_{number}'.
    Returns the number of articles ingested.
    """
    collection = get_collection()
    articles = law.get("articles", [])
    if not articles:
        return 0

    batch_size = 64
    count = 0

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]

        # Deduplicate IDs — some laws have duplicate article numbers
        seen = {}
        raw_ids = []
        for a in batch:
            base = f"{law['slug']}_clan_{a['number']}"
            seen[base] = seen.get(base, 0) + 1
            raw_ids.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        ids = raw_ids
        documents = [f"Član {a['number']}. {a['text']}" for a in batch]
        metadatas = [
            {
                "law_slug": law["slug"],
                "law_title": law["title"],
                "gazette": law.get("gazette", ""),
                "article_number": str(a["number"]),
                "chapter": a.get("chapter", ""),
                "source_url": law.get("source_url", ""),
                # Enhanced metadata from Sprint 1
                "doc_type": law.get("doc_type", "zakon"),
                "authority_level": law.get("authority_level", 3),
                "valid_from": law.get("valid_from", ""),
                "stav_count": a.get("stav_count", 0),
                "tacka_count": a.get("tacka_count", 0),
            }
            for a in batch
        ]

        embeddings = embed_texts(documents)

        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        count += len(batch)

    return count


def _slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def ingest_client_document(title: str, content: str, metadata: dict | None = None) -> dict:
    """Split content into ~500 char chunks, embed, store in client_documents collection.

    Returns {"doc_id": str, "chunks": int}.
    """
    collection = get_client_collection()
    metadata = metadata or {}

    doc_id = f"{_slugify(title)}_{int(time.time())}"

    # Split into ~500 char chunks (break on sentence boundaries when possible)
    chunks = []
    remaining = content
    while remaining:
        if len(remaining) <= 500:
            chunks.append(remaining)
            break
        # Try to break at a sentence boundary near 500 chars
        cut = remaining[:500]
        last_period = max(cut.rfind(". "), cut.rfind(".\n"))
        if last_period > 200:
            chunks.append(remaining[: last_period + 1])
            remaining = remaining[last_period + 1 :].lstrip()
        else:
            # Fall back to space break
            last_space = cut.rfind(" ")
            if last_space > 200:
                chunks.append(remaining[:last_space])
                remaining = remaining[last_space + 1 :]
            else:
                chunks.append(cut)
                remaining = remaining[500:]

    if not chunks:
        return {"doc_id": doc_id, "chunks": 0}

    ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "doc_id": doc_id,
            "doc_title": title,
            "chunk_index": str(i),
            "total_chunks": str(len(chunks)),
            "source": "client_upload",
            **{k: str(v) for k, v in metadata.items()},
        }
        for i in range(len(chunks))
    ]

    embeddings = embed_texts(chunks)

    collection.upsert(
        ids=ids,
        documents=chunks,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    return {"doc_id": doc_id, "chunks": len(chunks)}


def ingest_court_decision(decision_id: int, chunks: list[dict]) -> int:
    """Ingest court decision chunks into the core serbian_laws collection.

    Each chunk dict should have: text, chunk_type, metadata (dict with court, date, etc.)
    Returns number of chunks ingested.
    """
    collection = get_collection()
    if not chunks:
        return 0

    batch_size = 64
    count = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        ids = [f"court_{decision_id}_chunk_{i + j}" for j, _ in enumerate(batch)]
        documents = [c["text"] for c in batch]
        metadatas = [c["metadata"] for c in batch]

        embeddings = embed_texts(documents)
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        count += len(batch)

    return count


def delete_client_document(doc_id: str) -> bool:
    """Remove all chunks for a client document. Returns True if any were deleted."""
    collection = get_client_collection()
    # Get existing chunks first to check if doc exists
    existing = collection.get(where={"doc_id": doc_id})
    if not existing["ids"]:
        return False
    collection.delete(where={"doc_id": doc_id})
    return True


def list_client_documents(include_preview: bool = False) -> list[dict]:
    """Return list of unique client documents with chunk counts.

    If include_preview is True, also returns a content preview and created_at
    extracted from the doc_id timestamp.
    """
    from datetime import datetime, timezone

    collection = get_client_collection()
    total = collection.count()
    if total == 0:
        return []

    includes = ["metadatas"]
    if include_preview:
        includes.append("documents")

    all_data = collection.get(include=includes)
    docs: dict[str, dict] = {}
    for i, meta in enumerate(all_data["metadatas"]):
        did = meta.get("doc_id", "")
        if did not in docs:
            # Extract timestamp from doc_id (format: slug_timestamp)
            created_at = ""
            try:
                ts = int(did.rsplit("_", 1)[-1])
                created_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except (ValueError, IndexError, OSError):
                pass

            docs[did] = {
                "doc_id": did,
                "title": meta.get("doc_title", ""),
                "category": meta.get("category", ""),
                "chunk_count": 0,
                "created_at": created_at,
            }
            if include_preview:
                docs[did]["content_preview"] = ""

        docs[did]["chunk_count"] += 1

        # Use the first chunk (chunk_index 0) as preview
        if include_preview and meta.get("chunk_index") == "0" and "documents" in all_data:
            text = all_data["documents"][i]
            docs[did]["content_preview"] = text[:200] + ("..." if len(text) > 200 else "")

    return list(docs.values())


def _keyword_boost(query: str, text: str) -> float:
    """Calculate keyword overlap bonus. Returns 0.0 to 0.15 boost."""
    from core.tokenizer import extract_query_keywords
    query_words = extract_query_keywords(query)
    if not query_words:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for w in query_words if w in text_lower)
    ratio = matches / len(query_words)
    return ratio * 0.15  # max 0.15 boost


def search(
    query: str, top_k: int = 5, law_filter: str | None = None
) -> list[dict]:
    """Hybrid search: vector similarity + keyword boosting.

    Searches BOTH core_laws and client_documents collections.
    Merges results by boosted score and returns top_k total.
    """
    query_embedding = embed_query(query)
    # Fetch more candidates for re-ranking
    fetch_k = max(top_k * 4, 20)
    results = []

    # Search core_laws
    core = get_collection()
    where = {"law_slug": law_filter} if law_filter else None
    core_count = core.count()
    if core_count > 0:
        core_top_k = min(fetch_k, core_count)
        core_results = core.query(
            query_embeddings=[query_embedding],
            n_results=core_top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        for idx in range(len(core_results["ids"][0])):
            text = core_results["documents"][0][idx]
            vec_score = 1.0 - core_results["distances"][0][idx]
            boost = _keyword_boost(query, text)
            results.append(
                {
                    "id": core_results["ids"][0][idx],
                    "text": text,
                    "metadata": core_results["metadatas"][0][idx],
                    "score": round(vec_score + boost, 4),
                    "vec_score": round(vec_score, 4),
                    "keyword_boost": round(boost, 4),
                    "source_collection": "core_laws",
                }
            )

    # Search client_documents
    client_col = get_client_collection()
    client_count = client_col.count()
    if client_count > 0:
        client_top_k = min(fetch_k, client_count)
        client_results = client_col.query(
            query_embeddings=[query_embedding],
            n_results=client_top_k,
            include=["documents", "metadatas", "distances"],
        )
        for idx in range(len(client_results["ids"][0])):
            text = client_results["documents"][0][idx]
            vec_score = 1.0 - client_results["distances"][0][idx]
            boost = _keyword_boost(query, text)
            results.append(
                {
                    "id": client_results["ids"][0][idx],
                    "text": text,
                    "metadata": client_results["metadatas"][0][idx],
                    "score": round(vec_score + boost, 4),
                    "vec_score": round(vec_score, 4),
                    "keyword_boost": round(boost, 4),
                    "source_collection": "client_documents",
                }
            )

    # Sort by score descending, return top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _authority_weight(authority_level: int | str) -> float:
    """Legal hierarchy boost. Higher authority = bigger boost."""
    try:
        level = int(authority_level)
    except (ValueError, TypeError):
        return 0.0
    return {1: 0.08, 2: 0.05, 3: 0.02, 4: 0.0, 5: 0.0}.get(level, 0.0)


def search_with_filters(
    query: str,
    top_k: int = 5,
    fetch_k: int = 40,
    law_filter: str | None = None,
    doc_types: list[str] | None = None,
    min_authority: int | None = None,
    include_client_docs: bool = True,
    reference_date: str | None = None,
) -> list[dict]:
    """Enhanced search with metadata filtering, authority weighting, and reranking.

    Args:
        reference_date: ISO date string (YYYY-MM-DD). If provided, filters to
            laws valid on that date (valid_from <= date AND valid_to >= date or empty).
    Returns candidates ready for cross-encoder reranking.
    """
    query_embedding = embed_query(query)
    results = []

    # Build ChromaDB where filter
    where_conditions = []
    if law_filter:
        where_conditions.append({"law_slug": law_filter})
    if doc_types:
        where_conditions.append({"doc_type": {"$in": doc_types}})
    if min_authority is not None:
        where_conditions.append({"authority_level": {"$lte": min_authority}})
    if reference_date:
        # Temporal filter: valid_from <= date AND (valid_to >= date OR valid_to is empty)
        # ChromaDB string comparison works for ISO dates (YYYY-MM-DD)
        where_conditions.append({"valid_from": {"$lte": reference_date}})

    where = None
    if len(where_conditions) == 1:
        where = where_conditions[0]
    elif len(where_conditions) > 1:
        where = {"$and": where_conditions}

    # Search core_laws
    core = get_collection()
    core_count = core.count()
    if core_count > 0:
        core_top_k = min(fetch_k, core_count)
        try:
            core_results = core.query(
                query_embeddings=[query_embedding],
                n_results=core_top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            # Filter might fail if metadata fields don't exist yet; fallback
            core_results = core.query(
                query_embeddings=[query_embedding],
                n_results=core_top_k,
                include=["documents", "metadatas", "distances"],
            )

        for idx in range(len(core_results["ids"][0])):
            text = core_results["documents"][0][idx]
            meta = core_results["metadatas"][0][idx]
            vec_score = 1.0 - core_results["distances"][0][idx]
            kw_boost = _keyword_boost(query, text)
            auth_boost = _authority_weight(meta.get("authority_level", 5))
            total = vec_score + kw_boost + auth_boost
            results.append({
                "id": core_results["ids"][0][idx],
                "text": text,
                "document": text,
                "metadata": meta,
                "score": round(total, 4),
                "vec_score": round(vec_score, 4),
                "keyword_boost": round(kw_boost, 4),
                "authority_boost": round(auth_boost, 4),
                "source_collection": "core_laws",
            })

    # Search client_documents
    if include_client_docs:
        client_col = get_client_collection()
        client_count = client_col.count()
        if client_count > 0:
            client_top_k = min(max(fetch_k // 4, 5), client_count)
            client_results = client_col.query(
                query_embeddings=[query_embedding],
                n_results=client_top_k,
                include=["documents", "metadatas", "distances"],
            )
            for idx in range(len(client_results["ids"][0])):
                text = client_results["documents"][0][idx]
                vec_score = 1.0 - client_results["distances"][0][idx]
                kw_boost = _keyword_boost(query, text)
                results.append({
                    "id": client_results["ids"][0][idx],
                    "text": text,
                    "document": text,
                    "metadata": client_results["metadatas"][0][idx],
                    "score": round(vec_score + kw_boost, 4),
                    "vec_score": round(vec_score, 4),
                    "keyword_boost": round(kw_boost, 4),
                    "authority_boost": 0.0,
                    "source_collection": "client_documents",
                })

    # ── BM25 Reciprocal Rank Fusion ─────────────────────────────────────────
    # Merge BM25 lexical scores with vector scores using RRF
    try:
        from rag.bm25 import bm25_search
        bm25_results = bm25_search(query, top_k=fetch_k)
        if bm25_results:
            # Build rank maps
            vec_rank = {r["id"]: i for i, r in enumerate(
                sorted(results, key=lambda x: x["score"], reverse=True)
            )}
            bm25_rank = {doc_id: i for i, (doc_id, _score) in enumerate(bm25_results)}
            bm25_score_map = {doc_id: score for doc_id, score in bm25_results}

            # RRF fusion: score = 1/(k+rank_vec) + 1/(k+rank_bm25)
            k = 60  # Standard RRF constant
            all_ids = set(vec_rank.keys()) | set(bm25_rank.keys())

            for r in results:
                rid = r["id"]
                v_rank = vec_rank.get(rid, len(results))
                b_rank = bm25_rank.get(rid, len(bm25_results) + 100)
                rrf = 1.0 / (k + v_rank) + 1.0 / (k + b_rank)
                r["rrf_score"] = round(rrf, 6)
                r["bm25_score"] = round(bm25_score_map.get(rid, 0.0), 4)

            # Add BM25-only results that vector search missed
            existing_ids = {r["id"] for r in results}
            for doc_id, bm25_sc in bm25_results[:20]:
                if doc_id not in existing_ids:
                    # Fetch from ChromaDB by ID
                    try:
                        fetched = get_collection().get(
                            ids=[doc_id],
                            include=["documents", "metadatas"],
                        )
                        if fetched["ids"]:
                            text = fetched["documents"][0]
                            meta = fetched["metadatas"][0]
                            # Respect doc_type and authority filters
                            if doc_types and meta.get("doc_type") not in doc_types:
                                continue
                            if min_authority is not None and meta.get("authority_level", 5) > min_authority:
                                continue
                            b_rank = bm25_rank.get(doc_id, 100)
                            rrf = 1.0 / (k + len(results)) + 1.0 / (k + b_rank)
                            results.append({
                                "id": doc_id,
                                "text": text,
                                "document": text,
                                "metadata": meta,
                                "score": 0.0,
                                "vec_score": 0.0,
                                "keyword_boost": 0.0,
                                "authority_boost": _authority_weight(meta.get("authority_level", 5)),
                                "bm25_score": round(bm25_sc, 4),
                                "rrf_score": round(rrf, 6),
                                "source_collection": "core_laws",
                            })
                    except Exception:
                        pass

            # Sort by RRF score
            results.sort(key=lambda x: x.get("rrf_score", x["score"]), reverse=True)
        else:
            results.sort(key=lambda x: x["score"], reverse=True)
    except Exception as e:
        # BM25 not available — fall back to vector-only
        import logging
        logging.getLogger("lexardor.store").debug("BM25 unavailable: %s", e)
        results.sort(key=lambda x: x["score"], reverse=True)

    return results[:fetch_k]


def get_stats() -> dict:
    """Return basic stats about the core laws vector store."""
    collection = get_collection()
    return {"total_articles": collection.count()}


def get_client_stats() -> dict:
    """Return stats about the client documents collection."""
    collection = get_client_collection()
    total_chunks = collection.count()
    docs = list_client_documents()
    return {
        "total_documents": len(docs),
        "total_chunks": total_chunks,
    }
