#!/usr/bin/env python3
"""Re-ingest the entire legal corpus with enhanced metadata.

Sprint 1: Reads all law JSON files, re-parses with enhanced parser
(sub-articles, cross-references, gazette versions, document classification),
populates SQLite legal schema, and updates ChromaDB metadata.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.reingest_corpus [--sqlite-only] [--dry-run] [--limit N]

Options:
    --sqlite-only   Only populate SQLite, skip ChromaDB re-embedding
    --dry-run       Parse and report stats without writing anything
    --limit N       Process only first N law files (for testing)
"""
import json
import sys
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.legal_schema import (
    init_legal_schema,
    upsert_legal_document,
    upsert_legal_article,
    insert_sub_articles,
    insert_citation_edges,
    insert_legal_versions,
)
from scraper.parser import (
    classify_document_type,
    parse_gazette_refs,
    derive_valid_from,
    extract_sub_articles,
    extract_cross_references,
)
from core.config import settings


def reingest_corpus(sqlite_only=False, dry_run=False, limit=None):
    """Main re-ingestion pipeline."""
    laws_dir = Path(settings.laws_path)
    if not laws_dir.exists():
        print(f"Laws directory not found: {laws_dir}")
        return

    json_files = sorted(laws_dir.glob("*.json"))
    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} law files in {laws_dir}")
    if dry_run:
        print("DRY RUN — no writes will be made\n")

    # Init schema
    if not dry_run:
        init_legal_schema()

    # Stats
    stats = {
        "files_processed": 0,
        "files_skipped": 0,
        "total_documents": 0,
        "total_articles": 0,
        "total_sub_articles": 0,
        "total_cross_refs": 0,
        "total_versions": 0,
        "by_type": {},
        "by_authority": {},
        "parse_errors": [],
    }

    start_time = time.time()

    for idx, json_path in enumerate(json_files):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            stats["files_skipped"] += 1
            stats["parse_errors"].append({"file": json_path.name, "error": str(e)})
            continue

        slug = data.get("slug", json_path.stem)
        title = data.get("title", slug)
        gazette = data.get("gazette", "")
        source_url = data.get("source_url", "")
        scraped_at = data.get("scraped_at", "")
        articles = data.get("articles", [])

        # Skip non-law files (news, guides, etc.) that have 'content' instead of 'articles'
        if not articles and "content" in data:
            stats["files_skipped"] += 1
            continue

        # Classify document type
        doc_type, authority_level = classify_document_type(title, slug)

        # Parse gazette versions
        gazette_refs = parse_gazette_refs(gazette or "")
        valid_from = derive_valid_from(gazette_refs)
        latest_gazette = gazette_refs[-1]["number"] if gazette_refs else ""
        gazette_numbers = [g["number"] for g in gazette_refs]

        # Track stats
        stats["total_documents"] += 1
        stats["by_type"][doc_type] = stats["by_type"].get(doc_type, 0) + 1
        stats["by_authority"][authority_level] = stats["by_authority"].get(authority_level, 0) + 1

        if dry_run:
            # Just count articles
            stats["total_articles"] += len(articles)
            for a in articles:
                text = a.get("text", "")
                subs = extract_sub_articles(text)
                refs = extract_cross_references(text, slug)
                stats["total_sub_articles"] += len(subs)
                stats["total_cross_refs"] += len(refs)
            stats["total_versions"] += len(gazette_refs)
            stats["files_processed"] += 1
            if (idx + 1) % 100 == 0:
                print(f"  [{idx + 1}/{len(json_files)}] Parsed {slug} ({len(articles)} articles)")
            continue

        # --- Write to SQLite ---
        doc_id = upsert_legal_document(
            slug=slug,
            title=title,
            doc_type=doc_type,
            authority_level=authority_level,
            gazette_ref=gazette or "",
            gazette_numbers=gazette_numbers,
            latest_gazette=latest_gazette,
            valid_from=valid_from,
            source_url=source_url,
            scraped_at=scraped_at,
            article_count=len(articles),
        )

        # Insert gazette versions
        if gazette_refs:
            insert_legal_versions(doc_id, gazette_refs)
            stats["total_versions"] += len(gazette_refs)

        # Process each article
        for a in articles:
            number = a.get("number", "")
            text = a.get("text", "")
            chapter = a.get("chapter", "") or ""
            chapter_number = a.get("chapter_number", "") or ""

            # Extract sub-articles
            subs = extract_sub_articles(text)
            stav_count = len(set(s["stav"] for s in subs if s.get("stav")))
            tacka_count = len([s for s in subs if s.get("tacka")])

            # Extract cross-references
            refs = extract_cross_references(text, slug)

            chroma_id = f"{slug}_clan_{number}"

            art_id = upsert_legal_article(
                document_id=doc_id,
                article_number=str(number),
                full_text=text,
                chapter=chapter,
                chapter_number=chapter_number,
                stav_count=stav_count,
                tacka_count=tacka_count,
                chroma_id=chroma_id,
            )

            if subs:
                insert_sub_articles(art_id, subs)
                stats["total_sub_articles"] += len(subs)

            if refs:
                insert_citation_edges(art_id, refs)
                stats["total_cross_refs"] += len(refs)

            stats["total_articles"] += 1

        stats["files_processed"] += 1

        if (idx + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (idx + 1) / elapsed
            eta = (len(json_files) - idx - 1) / rate if rate > 0 else 0
            print(f"  [{idx + 1}/{len(json_files)}] {slug} | "
                  f"{stats['total_articles']} articles | "
                  f"ETA: {eta:.0f}s")

    elapsed = time.time() - start_time

    # Print summary
    print(f"\n{'='*60}")
    print(f"RE-INGESTION {'(DRY RUN) ' if dry_run else ''}COMPLETE")
    print(f"{'='*60}")
    print(f"Time:            {elapsed:.1f}s")
    print(f"Files processed: {stats['files_processed']}")
    print(f"Files skipped:   {stats['files_skipped']}")
    print(f"Documents:       {stats['total_documents']}")
    print(f"Articles:        {stats['total_articles']}")
    print(f"Sub-articles:    {stats['total_sub_articles']}")
    print(f"Cross-refs:      {stats['total_cross_refs']}")
    print(f"Gazette versions:{stats['total_versions']}")
    print(f"\nBy type:")
    for dt, count in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
        print(f"  {dt}: {count}")
    print(f"\nBy authority level:")
    for al, count in sorted(stats["by_authority"].items()):
        labels = {1: "Ustav", 2: "Zakon/Zakonik", 3: "Uredba", 4: "Pravilnik/Odluka", 5: "Mišljenje/Ostalo"}
        print(f"  {al} ({labels.get(al, '?')}): {count}")

    if stats["parse_errors"]:
        print(f"\nParse errors ({len(stats['parse_errors'])}):")
        for err in stats["parse_errors"][:10]:
            print(f"  {err['file']}: {err['error']}")

    return stats


if __name__ == "__main__":
    args = sys.argv[1:]
    sqlite_only = "--sqlite-only" in args
    dry_run = "--dry-run" in args
    limit = None
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            limit = 10

    reingest_corpus(sqlite_only=sqlite_only, dry_run=dry_run, limit=limit)
