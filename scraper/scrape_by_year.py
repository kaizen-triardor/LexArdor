"""Scrape court decisions by year — more efficient captcha usage.

Each year has ~3-5K results. One captcha token covers ~75 pages = ~1875 results.
So most years need only 1-2 captcha tokens.

Usage:
    python scraper/scrape_by_year.py --year 2025 --url "https://sudskapraksa.sud.rs/sudska-praksa?..."
    python scraper/scrape_by_year.py --year 2025 --url "..." --rescan  # Force re-scan (ignore previous progress)
    python scraper/scrape_by_year.py --download   # Download files for all collected IDs
    python scraper/scrape_by_year.py --extract    # Extract text (all formats: PDF/DOC/ODT/DOCX/RTF)
    python scraper/scrape_by_year.py --status     # Show progress per year
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sudskapraksa.sud.rs"
DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "texts"
PROBLEM_DIR = DATA_DIR / "problematic"
MASTER_FILE = DATA_DIR / "_all_ids.json"
PER_PAGE = 25


def load_master() -> dict:
    if MASTER_FILE.exists():
        return json.loads(MASTER_FILE.read_text())
    return {"all_ids": [], "by_year": {}, "total": 0}


def save_master(m: dict):
    m["total"] = len(m["all_ids"])
    MASTER_FILE.write_text(json.dumps(m, indent=2))


def collect_year(year: int, captcha_url: str, phpsessid: str, rescan: bool = False):
    """Collect all IDs for a specific year.

    If rescan=True, ignores previous progress and starts from page 1,
    but still merges with existing IDs (never loses data).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    master = load_master()

    # Parse captcha URL params
    parsed = urlparse(captcha_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
    flat["godina"] = str(year)

    # Separate captcha params — send on first request only, strip for subsequent pages
    captcha_keys = {"g-recaptcha-response", "captcha", "Submit"}
    captcha_params = {k: flat.pop(k) for k in captcha_keys if k in flat}

    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="sudskapraksa.sud.rs")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/sudska-praksa",
    })

    existing_ids = set(master["all_ids"])
    year_ids = set(master.get("by_year", {}).get(str(year), []))
    year_progress = DATA_DIR / f"_year_{year}_progress.json"
    start_page = 1

    if rescan:
        print(f"\n  RESCAN year {year}: starting fresh from page 1 (keeping {len(year_ids)} existing IDs)")
    elif year_progress.exists():
        yp = json.loads(year_progress.read_text())
        start_page = yp.get("last_page", 0) + 1
        year_ids = set(yp.get("ids", []))

    print(f"\n  Year {year}: starting from page {start_page}, have {len(year_ids)} IDs")

    page = start_page
    consecutive_empty = 0
    new_this_run = 0

    while True:
        flat["page"] = str(page)

        # Include captcha params only on the first request to unlock the session
        request_params = {**flat}
        if page == 1 and captcha_params:
            request_params.update(captcha_params)

        try:
            r = session.get(f"{BASE_URL}/sudska-praksa", params=request_params, timeout=30)
            if r.status_code != 200:
                print(f"    Page {page}: HTTP {r.status_code}")
                consecutive_empty += 1
                if consecutive_empty > 3:
                    break
                time.sleep(3)
                page += 1
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"/sudska-praksa/download/id/(\d+)/file/odluka"))

            if not links:
                consecutive_empty += 1
                if consecutive_empty > 5:
                    print(f"    No results after page {page} — done or token expired")
                    break
                page += 1
                time.sleep(1)
                continue

            consecutive_empty = 0
            new_on_page = 0
            for link in links:
                m = re.search(r"/id/(\d+)/", link["href"])
                if m:
                    did = int(m.group(1))
                    if did not in existing_ids:
                        existing_ids.add(did)
                        year_ids.add(did)
                        new_on_page += 1
                        new_this_run += 1

            # Save progress every page
            yp_data = {"year": year, "last_page": page, "ids": sorted(year_ids), "total": len(year_ids)}
            year_progress.write_text(json.dumps(yp_data))

            if page % 20 == 0 or page <= 3:
                print(f"    Page {page}: +{new_on_page} new ({len(year_ids)} total for {year})")

            page += 1
            time.sleep(0.8)

        except KeyboardInterrupt:
            print(f"\n    Interrupted at page {page}")
            break
        except Exception as e:
            print(f"    Page {page} error: {e}")
            time.sleep(3)
            page += 1

    # Update master
    master["all_ids"] = sorted(existing_ids)
    master.setdefault("by_year", {})[str(year)] = sorted(year_ids)
    save_master(master)

    print(f"\n  Year {year} done: {len(year_ids)} IDs (+{new_this_run} new this run)")
    print(f"  Master total: {len(existing_ids)} IDs")
    return len(year_ids)


def download_all(phpsessid: str):
    """Download decision files for all collected IDs.

    Files are saved as .pdf regardless of actual format (server serves
    all formats from the same endpoint). The reextract_all.py script
    detects real file type via magic bytes during extraction.
    """
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    master = load_master()
    all_ids = master.get("all_ids", [])

    if not all_ids:
        print("  No IDs. Collect some years first.")
        return

    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="sudskapraksa.sud.rs")
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    downloaded = 0
    skipped = 0
    errors = 0

    print(f"  Downloading files for {len(all_ids)} IDs...")

    for i, did in enumerate(all_ids):
        pdf_path = PDF_DIR / f"{did}.pdf"
        if pdf_path.exists() and pdf_path.stat().st_size > 100:
            skipped += 1
            continue

        url = f"{BASE_URL}/sudska-praksa/download/id/{did}/file/odluka"
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 50:
                pdf_path.write_bytes(r.content)
                downloaded += 1
            elif r.status_code == 200:
                # Tiny response — could be an error page, save anyway
                pdf_path.write_bytes(r.content)
                downloaded += 1
            else:
                errors += 1
                with open(DATA_DIR / "download_errors.jsonl", "a") as f:
                    f.write(json.dumps({"id": did, "status": r.status_code}) + "\n")

            if (downloaded + skipped) % 200 == 0:
                print(f"    Progress: {downloaded} new + {skipped} existing + {errors} errors = {downloaded+skipped+errors}/{len(all_ids)}")

            time.sleep(0.3)
        except Exception as e:
            errors += 1
            time.sleep(1)

    print(f"\n  Download: {downloaded} new, {skipped} existing, {errors} errors")

    # Verify
    total_pdfs = len(list(PDF_DIR.glob("*.pdf")))
    missing = len(all_ids) - total_pdfs
    if missing > 0:
        print(f"  ⚠️ {missing} PDFs still missing. Run --download again.")
    else:
        print(f"  ✅ All {total_pdfs} PDFs downloaded!")


def extract_all():
    """Extract text from all downloaded files using universal format detection.

    Handles: PDF, DOC, DOCX, ODT, OTT, RTF, HTML, TXT
    Uses reextract_all.py for format detection and extraction.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from reextract_all import reextract
    reextract("all")


def show_status():
    master = load_master()
    total_pdfs = len(list(PDF_DIR.glob("*.pdf"))) if PDF_DIR.exists() else 0
    total_texts = len(list(TEXT_DIR.glob("*.json"))) if TEXT_DIR.exists() else 0
    total_probs = len(list(PROBLEM_DIR.glob("*.json"))) if PROBLEM_DIR.exists() else 0

    print(f"""
  ╔════════════════════════════════════════════════════╗
  ║  Court Decisions Scraper — Status by Year          ║
  ╠════════════════════════════════════════════════════╣
  ║  Total IDs:      {master['total']:>8}  / 74,241 target      ║
  ║  PDFs:           {total_pdfs:>8}                           ║
  ║  Good texts:     {total_texts:>8}                           ║
  ║  Problematic:    {total_probs:>8}  (scanned, need OCR)    ║
  ╠════════════════════════════════════════════════════╣""")

    by_year = master.get("by_year", {})
    if by_year:
        print("  ║  Per year:                                       ║")
        for yr in sorted(by_year.keys(), reverse=True):
            count = len(by_year[yr])
            print(f"  ║    {yr}: {count:>6} IDs                              ║")
    else:
        # Check old progress
        old_progress = DATA_DIR / "_download_progress.json"
        if old_progress.exists():
            op = json.loads(old_progress.read_text())
            print(f"  ║  (Legacy IDs: {op.get('total_ids', 0)})                         ║")

    print(f"  ╚════════════════════════════════════════════════════╝")

    # Missing years estimate
    collected_years = set(by_year.keys())
    all_years = set(str(y) for y in range(2008, 2027))
    missing = all_years - collected_years
    if missing:
        print(f"\n  Years not yet scraped: {', '.join(sorted(missing))}")


def merge_legacy():
    """Merge IDs from old _download_progress.json into master."""
    old_file = DATA_DIR / "_download_progress.json"
    if not old_file.exists():
        return

    old = json.loads(old_file.read_text())
    old_ids = set(old.get("all_ids", []))
    if not old_ids:
        return

    master = load_master()
    existing = set(master["all_ids"])
    new = old_ids - existing
    if new:
        master["all_ids"] = sorted(existing | old_ids)
        master.setdefault("by_year", {})["legacy"] = sorted(old_ids)
        save_master(master)
        print(f"  Merged {len(new)} legacy IDs into master ({len(master['all_ids'])} total)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Year to scrape (e.g. 2025)")
    parser.add_argument("--url", help="Full captcha URL from browser")
    parser.add_argument("--phpsessid", default="c2d9ce60bf6e994ca0ce712ab846d5ba")
    parser.add_argument("--download", action="store_true", help="Download files")
    parser.add_argument("--extract", action="store_true", help="Extract text (all formats)")
    parser.add_argument("--rescan", action="store_true", help="Force re-scan year from page 1")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--merge-legacy", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        show_status()
        return

    if args.merge_legacy:
        merge_legacy()
        return

    if args.year and args.url:
        collect_year(args.year, args.url, args.phpsessid, rescan=args.rescan)
        # Auto-download after collection
        download_all(args.phpsessid)
        extract_all()
        show_status()
        return

    if args.download:
        download_all(args.phpsessid)
        return

    if args.extract:
        extract_all()
        return

    show_status()
    print("\n  Usage:")
    print("    1. Open browser: https://sudskapraksa.sud.rs/sudska-praksa")
    print("    2. Set 'Година' to desired year (e.g. 2025)")
    print("    3. Solve captcha, click Претрага")
    print("    4. Copy full URL")
    print("    5. Run: python scraper/scrape_by_year.py --year 2025 --url 'URL'")
    print("    6. For re-scan: add --rescan flag to ignore previous progress")
    print("\n  Missing years: 2002-2007, incomplete: 2008 (91), 2009 (7)")


if __name__ == "__main__":
    main()
