#!/usr/bin/env python3
"""CLI script to scrape Serbian laws from paragraf.rs."""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from scraper.paragraf import scrape_laws, PRIORITY_SLUGS


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Serbian laws from paragraf.rs",
    )
    parser.add_argument(
        "slugs",
        nargs="*",
        help="Law slugs to scrape (default: priority list)",
    )
    parser.add_argument(
        "--all-priority",
        action="store_true",
        help="Scrape all priority laws",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=settings.laws_path,
        help=f"Output directory (default: {settings.laws_path})",
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=3.0,
        help="Delay between requests in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.all_priority:
        slugs = PRIORITY_SLUGS
    elif args.slugs:
        slugs = args.slugs
    else:
        parser.print_help()
        print("\nProvide law slugs or use --all-priority to scrape top 20 laws.")
        sys.exit(1)

    print(f"Scraping {len(slugs)} law(s) -> {args.output_dir}")
    saved = scrape_laws(slugs, args.output_dir, delay=args.delay)
    print(f"Successfully saved {len(saved)} law(s).")

    for path in saved:
        print(f"  {path}")


if __name__ == "__main__":
    main()
