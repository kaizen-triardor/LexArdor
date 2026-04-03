#!/usr/bin/env python3
"""CLI script: read all JSON files from data/laws/ and ingest into ChromaDB."""

import json
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from rag.store import get_stats, ingest_law


def main() -> None:
    laws_dir = Path(settings.laws_path)
    if not laws_dir.exists():
        print(f"Laws directory not found: {laws_dir}")
        sys.exit(1)

    json_files = sorted(laws_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files found in {laws_dir}")
        sys.exit(0)

    total = 0
    for fp in json_files:
        law = json.loads(fp.read_text(encoding="utf-8"))
        count = ingest_law(law)
        total += count
        print(f"  Ingested {count:>4} articles from {fp.name}")

    stats = get_stats()
    print(f"\nDone. Total articles in store: {stats['total_articles']}")


if __name__ == "__main__":
    main()
