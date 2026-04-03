"""Full scraper for sudskapraksa.sud.rs — all 4 sections.

Scrapes:
1. Sudska praksa (court decisions) — ~74,000
2. Pravna shvatanja (legal opinions)
3. Sentence (key legal positions/summaries)
4. Bilteni (practice bulletins)

Requires cookies from solve_captcha.py first.

Usage:
    # Step 1: Solve captcha first
    python scraper/solve_captcha.py

    # Step 2: Run full scraper
    python scraper/court_full_scraper.py

    # Step 3: Ingest into LexArdor
    python scraper/court_full_scraper.py --ingest
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sudskapraksa.sud.rs"
DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"

SECTIONS = {
    "sudska_praksa": {
        "url": f"{BASE_URL}/sudska-praksa",
        "name": "Судска пракса",
        "doc_type": "sudska_praksa",
    },
    "pravna_shvatanja": {
        "url": f"{BASE_URL}/pravna-shvatanja",
        "name": "Правна схватања",
        "doc_type": "pravno_shvatanje",
    },
    "sentence": {
        "url": f"{BASE_URL}/sentence",
        "name": "Сентенце",
        "doc_type": "sentenca",
    },
    "bilteni": {
        "url": f"{BASE_URL}/bilteni",
        "name": "Билтени",
        "doc_type": "bilten",
    },
}


def get_session() -> requests.Session:
    """Build session from saved cookies."""
    progress_file = DATA_DIR / "_progress.json"
    if not progress_file.exists():
        print("ERROR: No saved cookies. Run solve_captcha.py first!")
        sys.exit(1)

    progress = json.loads(progress_file.read_text())
    cookies = progress.get("cookies", {})
    if not cookies.get("PHPSESSID"):
        print("ERROR: No PHPSESSID cookie. Run solve_captcha.py first!")
        sys.exit(1)

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="sudskapraksa.sud.rs")

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sr,en;q=0.5",
        "Referer": BASE_URL,
    })
    return session


def parse_list_page(html: str) -> tuple[list[dict], int]:
    """Parse a search results / listing page. Returns (items, total_count)."""
    soup = BeautifulSoup(html, "html.parser")

    # Total count - look for patterns like "Резултата: 74.241" or badge with number
    total = 0
    for el in soup.find_all(["span", "div", "p", "h2", "strong"]):
        text = el.get_text(strip=True)
        match = re.search(r'(?:Укупно|Резултат[аи]?|Пронађено|Приказ)\s*:?\s*([\d.,]+)', text)
        if match:
            total = int(match.group(1).replace(".", "").replace(",", ""))
            break

    # Parse items
    items = []

    # Strategy 1: Look for links to individual decisions
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Match decision URLs like /sudska-praksa/12345 or /sentence/123
        if re.match(r'^/(sudska-praksa|pravna-shvatanja|sentence|bilteni)/\d+', href):
            title = link.get_text(strip=True)
            if title and len(title) > 5:
                item = {
                    "url": BASE_URL + href if href.startswith("/") else href,
                    "title": title,
                }
                # Look for surrounding metadata
                parent = link.parent
                if parent:
                    for sib in parent.find_all(["span", "small", "div"], limit=5):
                        sib_text = sib.get_text(strip=True)
                        if re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', sib_text):
                            item["date"] = sib_text
                        elif re.search(r'(?:Рев|Прев|Гж|Кж|Уж|Рж)\s*\.?\s*\d+/\d+', sib_text):
                            item["case_number"] = sib_text
                items.append(item)

    # Strategy 2: Look for "Текст одлуке" links specifically
    if not items:
        for link in soup.find_all("a", string=re.compile(r"Текст|текст|одлук|Одлук")):
            href = link.get("href", "")
            if href:
                items.append({
                    "url": BASE_URL + href if href.startswith("/") else href,
                    "title": link.get_text(strip=True),
                })

    # Deduplicate by URL
    seen = set()
    unique = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    return unique, total


def fetch_decision(session: requests.Session, url: str) -> dict:
    """Fetch full text of a single decision."""
    try:
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "url": url}

        soup = BeautifulSoup(r.text, "html.parser")
        data = {"source_url": url}

        # Title
        h1 = soup.find("h1") or soup.find("h2")
        if h1:
            data["title"] = h1.get_text(strip=True)

        # Metadata from tables or definition lists
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                val = cells[1].get_text(strip=True)
                if any(k in key for k in ["суд", "sud"]):
                    data["court"] = val
                elif any(k in key for k in ["број", "broj"]):
                    data["case_number"] = val
                elif any(k in key for k in ["датум", "datum"]):
                    data["date"] = val
                elif any(k in key for k in ["материја", "materija"]):
                    data["legal_domain"] = val
                elif any(k in key for k in ["врста", "vrsta"]):
                    data["decision_type"] = val

        # Also check dt/dd pairs
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                key = dt.get_text(strip=True).lower()
                val = dd.get_text(strip=True)
                if "суд" in key: data["court"] = val
                elif "број" in key: data["case_number"] = val
                elif "датум" in key: data["date"] = val
                elif "материја" in key: data["legal_domain"] = val

        # Full text - "Текст одлуке" section
        text_link = soup.find("a", string=re.compile(r"Текст|текст"))
        if text_link and text_link.get("href"):
            text_url = text_link["href"]
            if text_url.startswith("/"):
                text_url = BASE_URL + text_url
            try:
                tr = session.get(text_url, timeout=30)
                if tr.status_code == 200:
                    text_soup = BeautifulSoup(tr.text, "html.parser")
                    content = text_soup.find("div", class_=re.compile(r"content|text|body")) or text_soup.find("article") or text_soup.find("main")
                    if content:
                        data["full_text"] = content.get_text(separator="\n", strip=True)
            except:
                pass

        # Fallback: get text from the decision page itself
        if "full_text" not in data:
            content = (soup.find("div", class_=re.compile(r"content|text|body|odluka")) or
                      soup.find("article") or soup.find("main"))
            if content:
                # Remove nav elements
                for nav in content.find_all(["nav", "header", "footer"]):
                    nav.decompose()
                data["full_text"] = content.get_text(separator="\n", strip=True)

        return data
    except Exception as e:
        return {"error": str(e), "url": url}


def scrape_section(session: requests.Session, section_key: str, max_pages: int = 0):
    """Scrape one section completely."""
    section = SECTIONS[section_key]
    section_dir = DATA_DIR / section_key
    section_dir.mkdir(parents=True, exist_ok=True)

    progress_file = section_dir / "_progress.json"
    if progress_file.exists():
        progress = json.loads(progress_file.read_text())
    else:
        progress = {"last_page": 0, "total_scraped": 0, "total_expected": 0}

    start_page = progress["last_page"]
    per_page = 25

    print(f"\n{'='*60}")
    print(f"  Scraping: {section['name']} ({section_key})")
    print(f"  URL: {section['url']}")
    print(f"  Resume from page: {start_page + 1}")
    print(f"{'='*60}")

    page_num = start_page + 1
    consecutive_empty = 0

    while True:
        if max_pages and page_num > max_pages:
            break

        params = {
            "datum_unosa_ili_izmene": "any",
            "sort": "date_desc",
            "pp": str(per_page),
            "page": str(page_num),
        }

        try:
            r = session.get(section["url"], params=params, timeout=30)
            if r.status_code != 200:
                print(f"  Page {page_num}: HTTP {r.status_code}")
                if r.status_code == 403:
                    print("  Session expired! Run solve_captcha.py again.")
                    break
                consecutive_empty += 1
                if consecutive_empty > 3:
                    break
                time.sleep(5)
                continue

            items, total = parse_list_page(r.text)

            if total > 0 and progress["total_expected"] == 0:
                progress["total_expected"] = total
                total_pages = (total + per_page - 1) // per_page
                print(f"  Total: {total} items, {total_pages} pages")

            if not items:
                consecutive_empty += 1
                if consecutive_empty > 3:
                    print(f"  No more results after page {page_num}")
                    break
                time.sleep(2)
                page_num += 1
                continue

            consecutive_empty = 0

            # Fetch full text for each item
            for i, item in enumerate(items):
                slug = item["url"].rstrip("/").split("/")[-1]
                out_file = section_dir / f"{slug}.json"

                if out_file.exists():
                    continue  # Already fetched

                decision = fetch_decision(session, item["url"])
                decision["listing"] = item
                decision["doc_type"] = section["doc_type"]

                out_file.write_text(json.dumps(decision, ensure_ascii=False, indent=2))
                progress["total_scraped"] += 1
                time.sleep(0.5)  # Polite delay

            progress["last_page"] = page_num
            progress_file.write_text(json.dumps(progress, indent=2))

            if page_num % 10 == 0 or page_num <= 3:
                total_pages = (progress["total_expected"] + per_page - 1) // per_page if progress["total_expected"] else "?"
                print(f"  Page {page_num}/{total_pages}: {progress['total_scraped']} scraped, {len(items)} on this page")

            page_num += 1
            time.sleep(1)  # Polite delay between pages

        except KeyboardInterrupt:
            print(f"\n  Interrupted at page {page_num}. Progress saved.")
            progress_file.write_text(json.dumps(progress, indent=2))
            return
        except Exception as e:
            print(f"  Page {page_num} error: {e}")
            time.sleep(5)
            page_num += 1

    progress_file.write_text(json.dumps(progress, indent=2))
    print(f"\n  Section {section_key} done: {progress['total_scraped']} scraped")


def ingest_all():
    """Ingest all scraped court data into LexArdor ChromaDB."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from rag.store import get_collection

    collection = get_collection()
    initial = collection.count()
    ingested = 0

    for section_key in SECTIONS:
        section_dir = DATA_DIR / section_key
        if not section_dir.exists():
            continue

        files = sorted(section_dir.glob("*.json"))
        files = [f for f in files if not f.name.startswith("_")]
        print(f"\n  Ingesting {section_key}: {len(files)} files")

        batch_ids, batch_docs, batch_metas = [], [], []
        batch_size = 50

        for f in files:
            try:
                data = json.loads(f.read_text())
            except:
                continue

            text = data.get("full_text", "")
            if not text or len(text) < 50:
                continue

            slug = f.stem
            doc_id = f"court_{section_key}_{slug}"
            doc_type = data.get("doc_type", SECTIONS[section_key]["doc_type"])

            batch_ids.append(doc_id)
            batch_docs.append(text[:8000])
            batch_metas.append({
                "law_slug": doc_id,
                "law_title": data.get("title", data.get("listing", {}).get("title", slug)),
                "doc_type": doc_type,
                "authority_level": 2,
                "gazette": data.get("case_number", ""),
                "article_number": "",
                "chapter": data.get("legal_domain", ""),
                "source_url": data.get("source_url", ""),
                "valid_from": data.get("date", ""),
            })

            if len(batch_ids) >= batch_size:
                try:
                    collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                    ingested += len(batch_ids)
                except Exception as e:
                    print(f"    Batch error: {e}")
                batch_ids, batch_docs, batch_metas = [], [], []

        # Flush remaining
        if batch_ids:
            try:
                collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                ingested += len(batch_ids)
            except Exception as e:
                print(f"    Final batch error: {e}")

        print(f"    Ingested from {section_key}: {ingested}")

    print(f"\n  Total ingested: {ingested}")
    print(f"  Corpus now: {collection.count()} (was {initial})")


def show_status():
    """Show scraping progress for all sections."""
    print(f"\n{'='*60}")
    print(f"  Scraping Status — sudskapraksa.sud.rs")
    print(f"{'='*60}")
    for key in SECTIONS:
        section_dir = DATA_DIR / key
        progress_file = section_dir / "_progress.json"
        if progress_file.exists():
            p = json.loads(progress_file.read_text())
            files = len(list(section_dir.glob("*.json"))) - 1  # Exclude _progress.json
            print(f"\n  {SECTIONS[key]['name']} ({key}):")
            print(f"    Scraped: {p.get('total_scraped', 0)}/{p.get('total_expected', '?')}")
            print(f"    Last page: {p.get('last_page', 0)}")
            print(f"    Files: {files}")
        else:
            print(f"\n  {SECTIONS[key]['name']} ({key}): NOT STARTED")


def main():
    parser = argparse.ArgumentParser(description="Full scraper for sudskapraksa.sud.rs")
    parser.add_argument("--section", choices=list(SECTIONS.keys()) + ["all"], default="all")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit pages per section (0=unlimited)")
    parser.add_argument("--ingest", action="store_true", help="Ingest scraped data into LexArdor")
    parser.add_argument("--status", action="store_true", help="Show progress")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.ingest:
        ingest_all()
        return

    session = get_session()
    print(f"  Session: PHPSESSID={session.cookies.get('PHPSESSID', 'N/A')[:15]}...")

    if args.section == "all":
        for key in SECTIONS:
            scrape_section(session, key, max_pages=args.max_pages)
    else:
        scrape_section(session, args.section, max_pages=args.max_pages)

    show_status()


if __name__ == "__main__":
    main()
