#!/usr/bin/env python3
"""Ingest extracted OpenClaw documents into ChromaDB.

Reads from data/openclaw_texts/ (produced by extract_remaining_docs.py)
and ingests into the serbian_laws ChromaDB collection.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.ingest_openclaw [--limit N] [--status]
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.store import ingest_court_decision  # Reuse same function for chunk ingestion

DATA_DIR = Path(__file__).parent.parent / "data"
OPENCLAW_DIR = DATA_DIR / "openclaw_texts"

# Document type mapping
DOC_TYPE_MAP = {
    "istorijski_zakon": ("istorijski_zakon", 2),     # Historical law → authority 2
    "propis": ("propis", 3),                          # Regulation → authority 3
    "strucni_tekst": ("strucni_tekst", 5),           # Professional text → authority 5
}


def is_garbled(text: str) -> bool:
    """Detect garbled encoding (Latin Extended codepoints instead of Cyrillic)."""
    sample = text[:500]
    latin_ext = len(re.findall(r"[\u0240-\u02FF]", sample))
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", sample))
    return latin_ext > 20 and latin_ext > cyrillic


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


def ingest_openclaw(limit=None):
    """Ingest OpenClaw extracted texts into ChromaDB."""
    if not OPENCLAW_DIR.exists():
        print("No openclaw_texts directory. Run extract_remaining_docs.py --openclaw first.")
        return

    json_files = sorted(OPENCLAW_DIR.glob("*.json"))
    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} OpenClaw extracted texts")

    total_chunks = 0
    total_docs = 0
    skipped_garbled = 0
    skipped_short = 0
    start = time.time()

    for idx, path in enumerate(json_files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        text = data.get("full_text", "")
        if not text or len(text) < 100:
            skipped_short += 1
            continue

        if is_garbled(text):
            skipped_garbled += 1
            continue

        doc_id = data.get("id", path.stem)
        source_cat = data.get("source_category", "unknown")
        doc_type, authority = DOC_TYPE_MAP.get(data.get("doc_type", ""), ("ostalo", 5))
        filename = data.get("filename", "")

        # Create title from filename
        title = re.sub(r"[-_]", " ", Path(filename).stem).strip()
        if not title:
            title = doc_id

        # Chunk the text
        text_chunks = smart_chunk(text, max_chars=1500)
        if not text_chunks:
            skipped_short += 1
            continue

        chunks = []
        for i, chunk_text in enumerate(text_chunks):
            meta = {
                "doc_type": doc_type,
                "authority_level": authority,
                "court": "",
                "decision_date": "",
                "case_number": "",
                "source_url": data.get("source_url", ""),
                "decision_id": str(doc_id),
                "law_slug": f"openclaw_{doc_id}",
                "law_title": title,
                "gazette": "",
                "article_number": "",
                "chapter": source_cat,
                "chunk_type": f"openclaw_{i}",
            }
            chunks.append({"text": chunk_text, "metadata": meta})

        # Use a hash of doc_id for numeric ID
        numeric_id = abs(hash(doc_id)) % (10**9)
        count = ingest_court_decision(numeric_id, chunks)
        total_chunks += count
        total_docs += 1

        if (idx + 1) % 100 == 0:
            elapsed = time.time() - start
            print(f"  [{idx + 1}/{len(json_files)}] {total_docs} docs, {total_chunks} chunks ({elapsed:.0f}s)")

    elapsed = time.time() - start
    print(f"\nOpenClaw ingestion complete ({elapsed:.0f}s)")
    print(f"  Documents:      {total_docs}")
    print(f"  Chunks:         {total_chunks}")
    print(f"  Skipped garbled: {skipped_garbled}")
    print(f"  Skipped short:   {skipped_short}")


def show_status():
    """Show OpenClaw ingestion readiness."""
    if not OPENCLAW_DIR.exists():
        print("No extracted texts. Run extract_remaining_docs.py --openclaw first.")
        return

    files = list(OPENCLAW_DIR.glob("*.json"))
    garbled = 0
    short = 0
    good = 0
    for f in files:
        d = json.loads(f.read_text())
        text = d.get("full_text", "")
        if len(text) < 100:
            short += 1
        elif is_garbled(text):
            garbled += 1
        else:
            good += 1

    print(f"OpenClaw texts: {len(files)} total")
    print(f"  Good quality: {good}")
    print(f"  Garbled:      {garbled}")
    print(f"  Too short:    {short}")
    print(f"  Ready for ingestion: {good}")


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = None
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            limit = 50

    if "--status" in args:
        show_status()
    else:
        ingest_openclaw(limit=limit)
