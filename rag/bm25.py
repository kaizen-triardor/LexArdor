"""BM25 lexical search index for Serbian legal articles.

Complements ChromaDB vector search with exact term matching.
Uses Okapi BM25 via rank_bm25 library.

Architecture:
- Index is persisted to disk (pickle) after building
- On startup, loads from disk if available (~seconds vs minutes to rebuild)
- Rebuilt on demand via /api/admin/rebuild-bm25
- With 738K+ documents, in-memory build takes ~5-10 min and ~2-4 GB RAM
"""
import logging
import pickle
import time
import threading
from pathlib import Path
from typing import Optional

from rank_bm25 import BM25Okapi

from core.tokenizer import tokenize

log = logging.getLogger("lexardor.bm25")

# ── Persistence path ──────────────────────────────────────────────────────────

_INDEX_PATH = Path(__file__).parent.parent / "data" / "bm25_index.pkl"

# ── Module-level singleton ───────────────────────────────────────────────────

_bm25_index: Optional["BM25Index"] = None
_build_lock = threading.Lock()


class BM25Index:
    """In-memory BM25 index over ChromaDB article texts."""

    def __init__(self):
        self.bm25: BM25Okapi | None = None
        self.doc_ids: list[str] = []       # ChromaDB IDs in same order as corpus
        self.doc_count: int = 0
        self.built_at: float = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and self.bm25 is not None

    def build(self, collection, persist: bool = True) -> dict:
        """Build BM25 index from a ChromaDB collection.

        Returns {ok, doc_count, build_time_s}.
        """
        t0 = time.time()
        log.info("Building BM25 index from ChromaDB...")

        total = collection.count()
        if total == 0:
            log.warning("ChromaDB collection is empty — BM25 index will be empty")
            self.bm25 = BM25Okapi([[""]])
            self.doc_ids = []
            self.doc_count = 0
            self._ready = True
            return {"ok": True, "doc_count": 0, "build_time_s": 0}

        # Fetch in batches
        batch_size = 5000
        all_ids = []
        all_texts = []

        for offset in range(0, total, batch_size):
            result = collection.get(
                limit=batch_size,
                offset=offset,
                include=["documents"],
            )
            if result and result.get("ids"):
                all_ids.extend(result["ids"])
                all_texts.extend(result["documents"])
            if offset % 50000 == 0 and offset > 0:
                log.info("  BM25 fetch progress: %d / %d", offset, total)

        log.info("Fetched %d documents, tokenizing...", len(all_ids))

        # Tokenize all documents
        tokenized_corpus = [tokenize(text, remove_stops=True) for text in all_texts]

        log.info("Tokenized, building BM25Okapi index...")

        # Build BM25 index
        self.bm25 = BM25Okapi(tokenized_corpus)
        self.doc_ids = all_ids
        self.doc_count = len(all_ids)
        self.built_at = time.time()
        self._ready = True

        elapsed = time.time() - t0
        log.info("BM25 index built: %d documents in %.1fs", self.doc_count, elapsed)

        # Persist to disk
        if persist:
            self._save()

        return {"ok": True, "doc_count": self.doc_count, "build_time_s": round(elapsed, 1)}

    def _save(self):
        """Persist the built index to disk."""
        try:
            _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "doc_ids": self.doc_ids,
                "doc_count": self.doc_count,
                "built_at": self.built_at,
                "bm25": self.bm25,
            }
            with open(_INDEX_PATH, "wb") as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            size_mb = _INDEX_PATH.stat().st_size / (1024 * 1024)
            log.info("BM25 index saved to disk: %.1f MB", size_mb)
        except Exception as e:
            log.error("Failed to save BM25 index: %s", e)

    def load(self) -> bool:
        """Load a previously persisted index from disk. Returns True on success."""
        if not _INDEX_PATH.exists():
            return False
        try:
            t0 = time.time()
            with open(_INDEX_PATH, "rb") as f:
                data = pickle.load(f)
            self.bm25 = data["bm25"]
            self.doc_ids = data["doc_ids"]
            self.doc_count = data["doc_count"]
            self.built_at = data["built_at"]
            self._ready = True
            elapsed = time.time() - t0
            log.info("BM25 index loaded from disk: %d docs in %.1fs", self.doc_count, elapsed)
            return True
        except Exception as e:
            log.error("Failed to load BM25 index from disk: %s", e)
            return False

    def search(self, query: str, top_k: int = 40) -> list[tuple[str, float]]:
        """Search the BM25 index.

        Returns list of (chroma_id, bm25_score) tuples, sorted by score descending.
        """
        if not self.ready:
            return []

        query_tokens = tokenize(query, remove_stops=True)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)

        # Get top-k indices by score
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                results.append((self.doc_ids[idx], float(scores[idx])))

        return results

    def status(self) -> dict:
        """Return index status for admin API."""
        return {
            "ready": self.ready,
            "doc_count": self.doc_count,
            "built_at": self.built_at,
            "persisted": _INDEX_PATH.exists(),
            "persisted_size_mb": round(_INDEX_PATH.stat().st_size / (1024 * 1024), 1) if _INDEX_PATH.exists() else 0,
        }


# ── Public API ───────────────────────────────────────────────────────────────

def get_bm25_index() -> BM25Index:
    """Get or create the BM25 index singleton."""
    global _bm25_index
    if _bm25_index is None:
        _bm25_index = BM25Index()
    return _bm25_index


def build_bm25_index(collection=None, background: bool = False) -> dict:
    """Build (or rebuild) the BM25 index.

    First tries to load from disk. If no persisted index exists,
    builds from ChromaDB (optionally in background).
    """
    idx = get_bm25_index()

    # Try loading from disk first
    if not idx.ready and idx.load():
        return {"ok": True, "doc_count": idx.doc_count, "source": "disk"}

    # Need to build from ChromaDB
    if collection is None:
        from rag.store import get_collection
        collection = get_collection()

    if background:
        def _build():
            with _build_lock:
                idx.build(collection)
        t = threading.Thread(target=_build, daemon=True, name="bm25-builder")
        t.start()
        return {"ok": True, "status": "building_in_background"}
    else:
        with _build_lock:
            return idx.build(collection)


def rebuild_bm25_index(collection=None) -> dict:
    """Force rebuild from ChromaDB (ignores disk cache)."""
    idx = get_bm25_index()
    if collection is None:
        from rag.store import get_collection
        collection = get_collection()
    with _build_lock:
        return idx.build(collection, persist=True)


def bm25_search(query: str, top_k: int = 40) -> list[tuple[str, float]]:
    """Search the BM25 index. Returns [(chroma_id, score)]."""
    idx = get_bm25_index()
    return idx.search(query, top_k=top_k)
