#!/usr/bin/env python3
"""Ingest additional documents (paragraf.rs scraped content) into ChromaDB.

Reads from data/additional-documents/*.json and ingests into the
serbian_laws ChromaDB collection with appropriate doc_type metadata.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.ingest_additional_docs [--status]
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.store import ingest_court_decision

DATA_DIR = Path(__file__).parent.parent / "data"
ADDITIONAL_DIR = DATA_DIR / "additional-documents"

# Map document categories to doc_type and authority_level
CATEGORY_MAP = {
    "court_practice": ("sudska_praksa", 2),
    "legal_forms": ("pravni_obrazac", 4),
    "legal_guides": ("strucni_tekst", 5),
    "legal_handbooks": ("strucni_tekst", 5),
    "legal_news": ("pravna_vest", 5),
    "legal_training": ("strucni_tekst", 5),
    "form": ("pravni_obrazac", 4),
    "guide": ("strucni_tekst", 5),
    "handbook": ("strucni_tekst", 5),
    "news": ("pravna_vest", 5),
    "training": ("strucni_tekst", 5),
}


def smart_chunk(text: str, max_chars: int = 1500, min_chars: int = 300) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text] if len(text) >= 50 else []

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            if len(remaining) >= min_chars // 2:
                chunks.append(remaining)
            elif chunks:
                chunks[-1] += " " + remaining
            break

        cut = remaining[:max_chars]
        last_period = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind(".\r"))
        if last_period > min_chars:
            chunks.append(remaining[:last_period + 1].strip())
            remaining = remaining[last_period + 1:].lstrip()
        else:
            last_nl = cut.rfind("\n")
            if last_nl > min_chars:
                chunks.append(remaining[:last_nl].strip())
                remaining = remaining[last_nl + 1:].lstrip()
            else:
                last_space = cut.rfind(" ")
                if last_space > min_chars:
                    chunks.append(remaining[:last_space].strip())
                    remaining = remaining[last_space + 1:].lstrip()
                else:
                    chunks.append(cut.strip())
                    remaining = remaining[max_chars:].lstrip()

    return [c for c in chunks if len(c) >= 50]


def ingest_additional():
    """Ingest additional documents into ChromaDB."""
    if not ADDITIONAL_DIR.exists():
        print("No additional-documents directory.")
        return

    json_files = sorted(ADDITIONAL_DIR.glob("*.json"))
    print(f"Found {len(json_files)} additional documents")

    total_chunks = 0
    total_docs = 0
    skipped_short = 0
    start = time.time()

    for idx, path in enumerate(json_files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  SKIP {path.name}: {e}")
            continue

        content = data.get("content", "")
        if not content or len(content) < 100:
            skipped_short += 1
            continue

        title = data.get("title", path.stem)
        source_url = data.get("source_url", "")
        category = data.get("category", data.get("document_type", "unknown"))
        slug = data.get("slug", path.stem)

        doc_type, authority = CATEGORY_MAP.get(category, ("strucni_tekst", 5))

        text_chunks = smart_chunk(content, max_chars=1500)
        if not text_chunks:
            skipped_short += 1
            continue

        chunks = []
        for i, chunk_text in enumerate(text_chunks):
            meta = {
                "doc_type": doc_type,
                "authority_level": authority,
                "court": "",
                "decision_date": data.get("scraped_at", "")[:10],
                "case_number": "",
                "source_url": source_url,
                "decision_id": slug,
                "law_slug": f"paragraf_{slug}",
                "law_title": title[:200],
                "gazette": "",
                "article_number": "",
                "chapter": category,
                "chunk_type": f"additional_{i}",
            }
            chunks.append({"text": chunk_text, "metadata": meta})

        numeric_id = abs(hash(slug)) % (10**9)
        count = ingest_court_decision(numeric_id, chunks)
        total_chunks += count
        total_docs += 1
        print(f"  [{idx + 1}/{len(json_files)}] {path.name}: {count} chunks")

    elapsed = time.time() - start
    print(f"\nAdditional docs ingestion complete ({elapsed:.0f}s)")
    print(f"  Documents: {total_docs}")
    print(f"  Chunks:    {total_chunks}")
    print(f"  Skipped:   {skipped_short}")


def show_status():
    """Show what's available for ingestion."""
    if not ADDITIONAL_DIR.exists():
        print("No additional-documents directory.")
        return

    files = list(ADDITIONAL_DIR.glob("*.json"))
    categories = {}
    for f in files:
        data = json.loads(f.read_text())
        cat = data.get("category", data.get("document_type", "unknown"))
        content = data.get("content", "")
        if cat not in categories:
            categories[cat] = {"count": 0, "chars": 0}
        categories[cat]["count"] += 1
        categories[cat]["chars"] += len(content)

    print(f"Additional documents: {len(files)} total")
    for cat, info in sorted(categories.items()):
        print(f"  {cat}: {info['count']} docs, {info['chars']:,} chars")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    else:
        ingest_additional()
