#!/usr/bin/env python3
"""Ingest court decisions and bilteni into ChromaDB with legal-aware chunking.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.ingest_court_decisions [--decisions] [--bilteni] [--limit N] [--status]
    python -m scripts.ingest_court_decisions --all
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.store import ingest_court_decision

DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
TEXTS_DIR = DATA_DIR / "texts"
BILTENI_DIR = DATA_DIR / "bilteni" / "texts"

# ── Court hierarchy for authority_level ──────────────────────────────────────

COURT_HIERARCHY = [
    # Level 1: Constitutional Court
    (1, [r"уставни\s*суд", r"ustavni\s*sud"]),
    # Level 2: Supreme Courts
    (2, [
        r"врховни\s*касациони", r"врховни\s*суд",
        r"vrhovni\s*kasacioni", r"vrhovni\s*sud",
    ]),
    # Level 3: Appellate Courts
    (3, [
        r"апелациони", r"виши\s*привредни",
        r"apelacioni", r"viši\s*privredni",
    ]),
    # Level 4: Higher/Commercial/Administrative Courts
    (4, [
        r"управни\s*суд", r"привредни\s*суд", r"виши\s*суд",
        r"upravni\s*sud", r"privredni\s*sud", r"viši\s*sud",
        r"привредни\s*апелациони",
    ]),
    # Level 5: Basic/Misdemeanor Courts (default)
    (5, [
        r"основни\s*суд", r"прекршајни",
        r"osnovni\s*sud", r"prekršajni",
    ]),
]


def classify_court(court_name: str) -> int:
    """Classify court authority level (1-5) from court name."""
    if not court_name:
        return 5
    lower = court_name.lower()
    for level, patterns in COURT_HIERARCHY:
        for pat in patterns:
            if re.search(pat, lower):
                return level
    return 5


# ── Section detection for court decisions ───────────────────────────────────

SECTION_MARKERS = {
    "reasoning": [
        r"О\s*б\s*р\s*а\s*з\s*л\s*о\s*ж\s*е\s*њ\s*е",
        r"ОБРАЗЛОЖЕЊЕ",
        r"Образложење",
        r"образложење",
        r"О\s*Б\s*Р\s*А\s*З\s*Л\s*О\s*Ж\s*Е\s*Њ\s*Е",
    ],
    "ruling": [
        r"Р\s*е\s*ш\s*а\s*в\s*а",
        r"РЕШАВА",
        r"П\s*Р\s*Е\s*С\s*У\s*Д\s*А",
        r"ПРЕСУДА",
        r"Пресуда",
        r"ПРЕСУЂУЈЕ",
        r"Р\s*Е\s*Ш\s*Е\s*Њ\s*Е",
        r"РЕШЕЊЕ",
    ],
}


def detect_sections(text: str) -> dict[str, str]:
    """Try to split court decision into sections (ruling, reasoning)."""
    sections = {}

    # Find reasoning section start
    reasoning_pos = -1
    for pat in SECTION_MARKERS["reasoning"]:
        m = re.search(pat, text)
        if m:
            reasoning_pos = m.start()
            break

    if reasoning_pos > 100:
        # Everything before reasoning marker is ruling/header
        sections["ruling"] = text[:reasoning_pos].strip()
        sections["reasoning"] = text[reasoning_pos:].strip()

    return sections


# ── Smart chunking ──────────────────────────────────────────────────────────

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
                # Append short tail to last chunk
                chunks[-1] += " " + remaining
            break

        cut = remaining[:max_chars]
        # Try to break at sentence end
        last_period = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind(".\r"))
        if last_period > min_chars:
            chunks.append(remaining[:last_period + 1].strip())
            remaining = remaining[last_period + 1:].lstrip()
        else:
            # Fall back to newline break
            last_nl = cut.rfind("\n")
            if last_nl > min_chars:
                chunks.append(remaining[:last_nl].strip())
                remaining = remaining[last_nl + 1:].lstrip()
            else:
                # Force break at space
                last_space = cut.rfind(" ")
                if last_space > min_chars:
                    chunks.append(remaining[:last_space].strip())
                    remaining = remaining[last_space + 1:].lstrip()
                else:
                    chunks.append(cut.strip())
                    remaining = remaining[max_chars:].lstrip()

    return [c for c in chunks if len(c) >= 50]


def chunk_decision(decision: dict) -> list[dict]:
    """Create chunks from a court decision with metadata."""
    text = decision.get("full_text", "")
    if not text or len(text) < 50:
        return []

    decision_id = decision.get("id", 0)
    court = decision.get("court", "")
    date = decision.get("date", "")
    case_number = decision.get("case_number", "")
    source_url = decision.get("source_url", "")
    authority = classify_court(court)

    base_meta = {
        "doc_type": "sudska_praksa",
        "authority_level": authority,
        "court": court or "",
        "decision_date": date or "",
        "case_number": case_number or "",
        "source_url": source_url or "",
        "decision_id": str(decision_id),
        "law_slug": f"court_{decision_id}",
        "law_title": f"{court} - {case_number}" if case_number else court or f"Decision {decision_id}",
        "gazette": "",
        "article_number": "",
        "chapter": "",
    }

    chunks = []

    # Try section-based chunking
    sections = detect_sections(text)
    if sections:
        for section_name, section_text in sections.items():
            section_chunks = smart_chunk(section_text, max_chars=1500)
            for i, chunk_text in enumerate(section_chunks):
                meta = {**base_meta, "chunk_type": section_name}
                if len(section_chunks) > 1:
                    meta["chunk_type"] = f"{section_name}_{i}"
                chunks.append({"text": chunk_text, "metadata": meta})
    else:
        # Fall back to generic smart chunking
        text_chunks = smart_chunk(text, max_chars=1500)
        for i, chunk_text in enumerate(text_chunks):
            meta = {**base_meta, "chunk_type": f"chunk_{i}" if len(text_chunks) > 1 else "full"}
            chunks.append({"text": chunk_text, "metadata": meta})

    return chunks


def chunk_bilten(bilten: dict) -> list[dict]:
    """Create chunks from a bilten with metadata."""
    text = bilten.get("full_text", "")
    if not text or len(text) < 50:
        return []

    bilten_id = bilten.get("id", 0)
    source_url = bilten.get("source_url", "")

    base_meta = {
        "doc_type": "bilten",
        "authority_level": 2,
        "court": "Врховни касациони суд",
        "decision_date": "",
        "case_number": "",
        "source_url": source_url or "",
        "decision_id": str(bilten_id),
        "law_slug": f"bilten_{bilten_id}",
        "law_title": f"Билтен {bilten_id}",
        "gazette": "",
        "article_number": "",
        "chapter": "",
        "chunk_type": "bilten",
    }

    text_chunks = smart_chunk(text, max_chars=1500)
    return [
        {"text": c, "metadata": {**base_meta, "chunk_type": f"bilten_{i}"}}
        for i, c in enumerate(text_chunks)
    ]


# ── Main ingestion ──────────────────────────────────────────────────────────

def ingest_decisions(limit=None):
    """Ingest court decisions into ChromaDB."""
    json_files = sorted(TEXTS_DIR.glob("*.json"))
    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} court decision texts")

    total_chunks = 0
    total_decisions = 0
    errors = 0
    start = time.time()

    for idx, path in enumerate(json_files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            errors += 1
            continue

        chunks = chunk_decision(data)
        if not chunks:
            continue

        decision_id = data.get("id", int(path.stem))
        count = ingest_court_decision(decision_id, chunks)
        total_chunks += count
        total_decisions += 1

        if (idx + 1) % 500 == 0:
            elapsed = time.time() - start
            rate = (idx + 1) / elapsed
            eta = (len(json_files) - idx - 1) / rate if rate > 0 else 0
            print(f"  [{idx + 1}/{len(json_files)}] {total_decisions} decisions, "
                  f"{total_chunks} chunks | {rate:.1f} docs/s | ETA: {eta/60:.0f}m")

    elapsed = time.time() - start
    print(f"\nCourt decisions ingestion complete ({elapsed:.0f}s)")
    print(f"  Decisions: {total_decisions}")
    print(f"  Chunks:    {total_chunks}")
    print(f"  Errors:    {errors}")
    return total_chunks


def ingest_bilteni(limit=None):
    """Ingest bilteni into ChromaDB."""
    json_files = sorted(BILTENI_DIR.glob("*.json"))
    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} bilteni texts")

    total_chunks = 0
    total_bilteni = 0

    for idx, path in enumerate(json_files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        chunks = chunk_bilten(data)
        if not chunks:
            continue

        bilten_id = data.get("id", int(path.stem))
        count = ingest_court_decision(bilten_id, chunks)
        total_chunks += count
        total_bilteni += 1

    print(f"\nBilteni ingestion complete")
    print(f"  Bilteni: {total_bilteni}")
    print(f"  Chunks:  {total_chunks}")
    return total_chunks


def show_status():
    """Show current ChromaDB collection status."""
    from rag.store import get_collection
    col = get_collection()
    total = col.count()
    print(f"\nChromaDB 'serbian_laws' collection: {total} documents")

    # Sample some entries to show doc_type distribution
    if total > 0:
        sample = col.get(limit=min(total, 10000), include=["metadatas"])
        types = {}
        for m in sample["metadatas"]:
            dt = m.get("doc_type", "unknown")
            types[dt] = types.get(dt, 0) + 1
        print(f"  Doc type distribution (sample of {len(sample['metadatas'])}):")
        for dt, count in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {dt}: {count}")


if __name__ == "__main__":
    args = sys.argv[1:]

    limit = None
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            limit = 100

    if "--status" in args:
        show_status()
    elif "--decisions" in args or "--all" in args:
        ingest_decisions(limit=limit)
        if "--all" in args:
            ingest_bilteni(limit=limit)
        show_status()
    elif "--bilteni" in args:
        ingest_bilteni(limit=limit)
        show_status()
    else:
        print("Usage:")
        print("  python -m scripts.ingest_court_decisions --decisions")
        print("  python -m scripts.ingest_court_decisions --bilteni")
        print("  python -m scripts.ingest_court_decisions --all")
        print("  python -m scripts.ingest_court_decisions --status")
        print("  Add --limit N to process only first N files")
        show_status()
