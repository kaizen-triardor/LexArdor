"""Court Practice Scraper for sudskapraksa.sud.rs

Downloads all ~74,000 court decisions from the Supreme Court portal.

Strategy:
1. Open browser, user manually solves reCAPTCHA once
2. Script captures the session cookies and captcha token
3. Paginate through all results using requests with captured cookies
4. Parse each decision page for structured data
5. Save to JSON files + ingest into LexArdor corpus

Usage:
    # Step 1: Run with --interactive to solve captcha manually
    python scraper/court_scraper.py --interactive

    # Step 2: After captcha solved, scraping starts automatically
    # Progress saved to data/court_decisions/ with resume capability
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Config
BASE_URL = "https://sudskapraksa.sud.rs"
SEARCH_URL = f"{BASE_URL}/sudska-praksa"
DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
PROGRESS_FILE = DATA_DIR / "_progress.json"
PER_PAGE = 25  # Max allowed by site


def load_progress() -> dict:
    """Load scraping progress for resume capability."""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"last_page": 0, "total_scraped": 0, "total_expected": 0, "errors": []}


def save_progress(progress: dict):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, ensure_ascii=False))


def interactive_captcha_solve():
    """Open browser for manual captcha solving, return cookies + token."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  KORAK 1: Ručno rešavanje reCAPTCHA")
    print("=" * 60)
    print("\n  Browser će se otvoriti sa sajtem sudskapraksa.sud.rs")
    print("  1. Klikni na 'Нисам робот' reCAPTCHA checkbox")
    print("  2. Reši CAPTCHA ako se pojavi")
    print("  3. Klikni 'Претрага' dugme")
    print("  4. Kada se prikažu rezultati, pritisni ENTER ovde\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Visible browser!
        context = browser.new_context()
        page = context.new_page()
        page.goto(SEARCH_URL)

        input("  >>> Reši CAPTCHA i klikni Pretraga, pa pritisni ENTER ovde... ")

        # Capture cookies and current URL
        cookies = context.cookies()
        current_url = page.url

        # Try to get captcha token from URL
        captcha_token = ""
        if "g-recaptcha-response=" in current_url:
            match = re.search(r'g-recaptcha-response=([^&]+)', current_url)
            if match:
                captcha_token = match.group(1)

        # Get captcha token from page
        if not captcha_token:
            captcha_token = page.evaluate("""() => {
                const el = document.querySelector('#g-recaptcha-response');
                return el ? el.value : '';
            }""")

        # Get total results count
        total_text = page.evaluate("""() => {
            const el = document.querySelector('.result-count, .total-results, h2');
            return el ? el.textContent : '';
        }""")

        # Parse page content to find total
        page_content = page.content()

        browser.close()

    # Build session cookies dict
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    print(f"\n  Cookies captured: {len(cookie_dict)}")
    print(f"  PHPSESSID: {cookie_dict.get('PHPSESSID', 'N/A')[:20]}...")
    print(f"  Captcha token: {captcha_token[:50]}..." if captcha_token else "  Captcha token: NOT FOUND")

    return cookie_dict, captcha_token, page_content


def parse_search_results(html: str) -> tuple[list[dict], int]:
    """Parse search results page, return (decisions, total_count)."""
    soup = BeautifulSoup(html, "html.parser")

    # Find total count
    total = 0
    count_el = soup.find("span", class_="badge") or soup.find(string=re.compile(r'Резултат[иа]?\s*:?\s*\d+'))
    if count_el:
        nums = re.findall(r'[\d.]+', str(count_el))
        if nums:
            total = int(nums[0].replace(".", ""))

    # Also try to find it in a different format
    if total == 0:
        text = soup.get_text()
        match = re.search(r'(?:Укупно|Резултат[аи]?|Пронађено)\s*:?\s*([\d.]+)', text)
        if match:
            total = int(match.group(1).replace(".", ""))

    # Parse individual decision entries
    decisions = []
    # Look for result items - common patterns on Serbian court sites
    items = soup.find_all("div", class_=re.compile(r"result|item|odluka|row"))
    if not items:
        items = soup.find_all("tr")  # Table layout
    if not items:
        items = soup.find_all("article")

    for item in items:
        # Try to extract decision data
        link = item.find("a", href=re.compile(r"/sudska-praksa/\d+|/odluka/"))
        if not link:
            continue

        decision = {
            "url": BASE_URL + link["href"] if link["href"].startswith("/") else link["href"],
            "title": link.get_text(strip=True),
        }

        # Extract metadata from the item
        meta_texts = [el.get_text(strip=True) for el in item.find_all(["span", "td", "small", "div"])
                      if el.get_text(strip=True) and el != link]

        # Try to parse common fields
        for text in meta_texts:
            if re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', text):
                decision["date"] = text
            elif re.search(r'(?:Рев|Прев|Гж|Кж|Уж|Рж)\s*\.?\s*\d+/\d+', text, re.IGNORECASE):
                decision["case_number"] = text
            elif any(k in text.lower() for k in ["суд", "sud"]):
                decision["court"] = text

        if decision.get("url"):
            decisions.append(decision)

    return decisions, total


def parse_decision_page(html: str) -> dict:
    """Parse individual court decision page for full text and metadata."""
    soup = BeautifulSoup(html, "html.parser")

    data = {}

    # Title / case number
    title = soup.find("h1") or soup.find("h2")
    if title:
        data["title"] = title.get_text(strip=True)

    # Look for structured metadata table
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True).lower()
            val = cells[1].get_text(strip=True)
            if "суд" in key or "sud" in key:
                data["court"] = val
            elif "број" in key or "broj" in key:
                data["case_number"] = val
            elif "датум" in key or "datum" in key:
                data["date"] = val
            elif "материја" in key or "materija" in key:
                data["legal_domain"] = val
            elif "врста" in key or "vrsta" in key:
                data["decision_type"] = val

    # Also check definition lists
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            key = dt.get_text(strip=True).lower()
            val = dd.get_text(strip=True)
            if "суд" in key:
                data["court"] = val
            elif "број" in key:
                data["case_number"] = val
            elif "датум" in key:
                data["date"] = val
            elif "материја" in key:
                data["legal_domain"] = val

    # Full text of the decision
    content_div = (soup.find("div", class_=re.compile(r"content|text|body|odluka")) or
                   soup.find("div", id=re.compile(r"content|text|body|odluka")) or
                   soup.find("article"))

    if content_div:
        data["full_text"] = content_div.get_text(separator="\n", strip=True)
    else:
        # Fallback - get main content area
        main = soup.find("main") or soup.find("div", class_="container")
        if main:
            data["full_text"] = main.get_text(separator="\n", strip=True)

    return data


def scrape_all(cookies: dict, captcha_token: str, first_page_html: str = ""):
    """Main scraping loop — paginate through all results."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value)

    # Headers to look like a real browser
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sr,en;q=0.5",
        "Referer": SEARCH_URL,
    })

    # Parse first page if provided
    start_page = progress.get("last_page", 0)
    total = progress.get("total_expected", 0)

    if first_page_html and start_page == 0:
        decisions, total = parse_search_results(first_page_html)
        print(f"  First page: {len(decisions)} decisions, total: {total}")
        progress["total_expected"] = total
        save_decisions(decisions, 1, session)
        progress["last_page"] = 1
        progress["total_scraped"] += len(decisions)
        save_progress(progress)

    if total == 0:
        total = 74241  # Known count from user

    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    print(f"\n  Total: ~{total} decisions, {total_pages} pages")
    print(f"  Resuming from page {start_page + 1}")

    for page_num in range(start_page + 1, total_pages + 1):
        offset = (page_num - 1) * PER_PAGE

        params = {
            "tip_suda": "",
            "pravna_materija": "",
            "upisnik": "",
            "predsednik_veca": "",
            "broj_predmeta": "",
            "godina": "",
            "datum_odluke[from]": "",
            "datum_odluke[to]": "",
            "vrsta_odluke": "",
            "pravnosnazna_odluka": "",
            "q": "",
            "datum_unosa_ili_izmene": "any",
            "sort": "date_desc",
            "pp": str(PER_PAGE),
            "page": str(page_num),
        }

        if captcha_token:
            params["g-recaptcha-response"] = captcha_token
            params["captcha"] = captcha_token

        try:
            r = session.get(SEARCH_URL, params=params, timeout=30)
            if r.status_code != 200:
                print(f"  Page {page_num}: HTTP {r.status_code}")
                progress["errors"].append({"page": page_num, "status": r.status_code})
                save_progress(progress)
                time.sleep(5)
                continue

            decisions, _ = parse_search_results(r.text)

            if not decisions:
                # Might need new captcha
                print(f"  Page {page_num}: No results (captcha expired?)")
                if page_num > start_page + 2:
                    # Save progress and exit — need new captcha
                    print("\n  CAPTCHA EXPIRED. Run again with --interactive to solve new captcha.")
                    save_progress(progress)
                    return
                continue

            save_decisions(decisions, page_num, session)

            progress["last_page"] = page_num
            progress["total_scraped"] += len(decisions)
            save_progress(progress)

            if page_num % 10 == 0:
                print(f"  Page {page_num}/{total_pages}: {progress['total_scraped']} scraped")

            # Polite delay
            time.sleep(1.5)

        except requests.exceptions.RequestException as e:
            print(f"  Page {page_num}: Error — {e}")
            progress["errors"].append({"page": page_num, "error": str(e)})
            save_progress(progress)
            time.sleep(10)

    print(f"\n  DONE: {progress['total_scraped']} decisions scraped")


def save_decisions(decisions: list[dict], page_num: int, session: requests.Session):
    """Save decisions from a search page. Optionally fetch full text."""
    batch_file = DATA_DIR / f"page_{page_num:05d}.json"

    # For now save the listing data — full text fetching is a separate pass
    with open(batch_file, "w", encoding="utf-8") as f:
        json.dump(decisions, f, ensure_ascii=False, indent=2)


def fetch_full_texts(cookies: dict, max_concurrent: int = 5):
    """Second pass: fetch full text for each decision."""
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html",
    })

    full_dir = DATA_DIR / "full_text"
    full_dir.mkdir(exist_ok=True)

    # Load all page files
    page_files = sorted(DATA_DIR.glob("page_*.json"))
    total = 0
    fetched = 0

    for pf in page_files:
        decisions = json.loads(pf.read_text())
        for d in decisions:
            total += 1
            url = d.get("url", "")
            if not url:
                continue

            # Check if already fetched
            slug = url.rstrip("/").split("/")[-1]
            out_file = full_dir / f"{slug}.json"
            if out_file.exists():
                fetched += 1
                continue

            try:
                r = session.get(url, timeout=30)
                if r.status_code == 200:
                    full_data = parse_decision_page(r.text)
                    full_data["source_url"] = url
                    full_data["listing"] = d
                    out_file.write_text(json.dumps(full_data, ensure_ascii=False, indent=2))
                    fetched += 1

                    if fetched % 100 == 0:
                        print(f"  Fetched {fetched}/{total} full texts")

                    time.sleep(1)
                else:
                    print(f"  {url}: HTTP {r.status_code}")
            except Exception as e:
                print(f"  {url}: {e}")

    print(f"\n  Full texts: {fetched}/{total}")


def ingest_to_lexardor():
    """Ingest scraped court decisions into LexArdor corpus."""
    full_dir = DATA_DIR / "full_text"
    if not full_dir.exists():
        print("No full texts found. Run fetch_full_texts first.")
        return

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from rag.store import get_collection
    from core.config import settings

    collection = get_collection()
    files = sorted(full_dir.glob("*.json"))

    print(f"  Ingesting {len(files)} court decisions...")

    batch_size = 50
    ingested = 0

    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        ids, docs, metas = [], [], []

        for f in batch:
            data = json.loads(f.read_text())
            text = data.get("full_text", "")
            if not text or len(text) < 100:
                continue

            slug = f.stem
            doc_id = f"court_{slug}"

            ids.append(doc_id)
            docs.append(text[:8000])  # Limit chunk size
            metas.append({
                "law_slug": f"court_{slug}",
                "law_title": data.get("title", slug),
                "doc_type": "sudska_praksa",
                "authority_level": 2,  # Court decisions = high authority
                "gazette": data.get("case_number", ""),
                "article_number": "",
                "chapter": data.get("legal_domain", ""),
                "source_url": data.get("source_url", ""),
                "valid_from": data.get("date", ""),
            })

        if ids:
            try:
                collection.add(ids=ids, documents=docs, metadatas=metas)
                ingested += len(ids)
                if ingested % 500 == 0:
                    print(f"  Ingested {ingested}/{len(files)}")
            except Exception as e:
                print(f"  Batch error: {e}")

    print(f"\n  DONE: {ingested} court decisions ingested into ChromaDB")
    print(f"  Total corpus now: {collection.count()} documents")


def main():
    parser = argparse.ArgumentParser(description="Court Practice Scraper for sudskapraksa.sud.rs")
    parser.add_argument("--interactive", action="store_true", help="Open browser for manual captcha solving")
    parser.add_argument("--fetch-full", action="store_true", help="Fetch full text for already scraped decisions")
    parser.add_argument("--ingest", action="store_true", help="Ingest scraped decisions into LexArdor")
    parser.add_argument("--status", action="store_true", help="Show scraping progress")
    args = parser.parse_args()

    if args.status:
        progress = load_progress()
        print(f"  Scraped: {progress.get('total_scraped', 0)}/{progress.get('total_expected', 0)}")
        print(f"  Last page: {progress.get('last_page', 0)}")
        print(f"  Errors: {len(progress.get('errors', []))}")
        page_files = list(DATA_DIR.glob("page_*.json"))
        full_files = list((DATA_DIR / "full_text").glob("*.json")) if (DATA_DIR / "full_text").exists() else []
        print(f"  Page files: {len(page_files)}")
        print(f"  Full text files: {len(full_files)}")
        return

    if args.fetch_full:
        progress = load_progress()
        # Reuse cookies from interactive session (saved in progress)
        cookies = progress.get("cookies", {})
        if not cookies:
            print("No saved cookies. Run with --interactive first.")
            return
        fetch_full_texts(cookies)
        return

    if args.ingest:
        ingest_to_lexardor()
        return

    if args.interactive:
        cookies, captcha_token, first_page = interactive_captcha_solve()
        # Save cookies for later reuse
        progress = load_progress()
        progress["cookies"] = cookies
        save_progress(progress)
        scrape_all(cookies, captcha_token, first_page)
    else:
        # Resume with saved cookies
        progress = load_progress()
        cookies = progress.get("cookies", {})
        if not cookies:
            print("No saved session. Run with --interactive first.")
            return
        scrape_all(cookies, "", "")


if __name__ == "__main__":
    main()
