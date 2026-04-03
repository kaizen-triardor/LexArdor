"""Court Decision Downloader — downloads all decisions from sudskapraksa.sud.rs

Uses captcha token for pagination and session cookie for PDF downloads.
Extracts text from PDFs and saves structured JSON for LexArdor ingestion.

Usage:
    python scraper/court_downloader.py --captcha-url "https://sudskapraksa.sud.rs/sudska-praksa?..." --phpsessid "abc123"
    python scraper/court_downloader.py --resume  # Resume from saved progress
    python scraper/court_downloader.py --ingest  # Ingest into LexArdor
    python scraper/court_downloader.py --status  # Check progress
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sudskapraksa.sud.rs"
DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "texts"
PROGRESS_FILE = DATA_DIR / "_download_progress.json"
PER_PAGE = 25


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text.strip()
    except ImportError:
        # Fallback: try pdfplumber
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
        except ImportError:
            return ""


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"last_page": 0, "total_ids": 0, "downloaded": 0, "extracted": 0,
            "all_ids": [], "captcha_url": "", "phpsessid": "", "errors": []}


def save_progress(p: dict):
    PROGRESS_FILE.write_text(json.dumps(p, indent=2, ensure_ascii=False))


def collect_ids(captcha_url: str, phpsessid: str, max_pages: int = 0) -> list[int]:
    """Phase 1: Paginate through search results and collect all decision IDs."""
    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="sudskapraksa.sud.rs")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/sudska-praksa",
    })

    # Parse the captcha URL to get base params
    parsed = urlparse(captcha_url)
    base_params = parse_qs(parsed.query, keep_blank_values=True)
    # Flatten single-value lists
    flat_params = {k: v[0] if len(v) == 1 else v for k, v in base_params.items()}

    progress = load_progress()
    all_ids = set(progress.get("all_ids", []))
    start_page = progress.get("last_page", 0) + 1

    print(f"  Collecting IDs starting from page {start_page}...")
    print(f"  Already have {len(all_ids)} IDs")

    page_num = start_page
    consecutive_empty = 0

    while True:
        if max_pages and page_num > max_pages:
            break

        flat_params["page"] = str(page_num)
        try:
            r = session.get(f"{BASE_URL}/sudska-praksa", params=flat_params, timeout=30)
            if r.status_code != 200:
                print(f"  Page {page_num}: HTTP {r.status_code}")
                consecutive_empty += 1
                if consecutive_empty > 5:
                    break
                time.sleep(3)
                continue

            # Extract download IDs from page
            soup = BeautifulSoup(r.text, "html.parser")
            download_links = soup.find_all("a", href=re.compile(r"/sudska-praksa/download/id/(\d+)/file/odluka"))

            if not download_links:
                consecutive_empty += 1
                if consecutive_empty > 5:
                    print(f"  No more results after page {page_num}")
                    break
                time.sleep(2)
                page_num += 1
                continue

            consecutive_empty = 0
            new_ids = []
            for link in download_links:
                match = re.search(r"/id/(\d+)/", link["href"])
                if match:
                    did = int(match.group(1))
                    if did not in all_ids:
                        all_ids.add(did)
                        new_ids.append(did)

            progress["last_page"] = page_num
            progress["all_ids"] = sorted(all_ids)
            progress["total_ids"] = len(all_ids)
            progress["captcha_url"] = captcha_url
            progress["phpsessid"] = phpsessid
            save_progress(progress)

            if page_num % 50 == 0 or page_num <= 5:
                print(f"  Page {page_num}: +{len(new_ids)} new IDs, total {len(all_ids)}")

            page_num += 1
            time.sleep(0.8)

        except KeyboardInterrupt:
            print(f"\n  Interrupted. {len(all_ids)} IDs saved.")
            save_progress(progress)
            return sorted(all_ids)
        except Exception as e:
            print(f"  Page {page_num}: {e}")
            time.sleep(5)
            page_num += 1

    save_progress(progress)
    print(f"\n  ID collection done: {len(all_ids)} total IDs")
    return sorted(all_ids)


def download_pdfs(phpsessid: str, max_downloads: int = 0):
    """Phase 2: Download PDFs for all collected IDs."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    progress = load_progress()
    all_ids = progress.get("all_ids", [])
    if not all_ids:
        print("  No IDs collected. Run ID collection first.")
        return

    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="sudskapraksa.sud.rs")
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    downloaded = 0
    skipped = 0
    errors = 0

    print(f"  Downloading PDFs for {len(all_ids)} decisions...")

    for i, did in enumerate(all_ids):
        if max_downloads and downloaded >= max_downloads:
            break

        pdf_path = PDF_DIR / f"{did}.pdf"
        if pdf_path.exists():
            skipped += 1
            continue

        url = f"{BASE_URL}/sudska-praksa/download/id/{did}/file/odluka"
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 100:
                pdf_path.write_bytes(r.content)
                downloaded += 1
            elif r.status_code == 200:
                # Tiny response — save anyway, might be a stub
                pdf_path.write_bytes(r.content)
                downloaded += 1
            else:
                # Non-200 — log error but retry later
                errors += 1
                error_log = DATA_DIR / "download_errors.jsonl"
                with open(error_log, "a") as f:
                    f.write(json.dumps({"id": did, "status": r.status_code, "time": time.strftime("%Y-%m-%d %H:%M")}) + "\n")

            if downloaded % 100 == 0:
                print(f"  Downloaded {downloaded}/{len(all_ids)} (skipped {skipped}, errors {errors})")

            time.sleep(0.3)
        except Exception as e:
            errors += 1
            error_log = DATA_DIR / "download_errors.jsonl"
            with open(error_log, "a") as f:
                f.write(json.dumps({"id": did, "error": str(e), "time": time.strftime("%Y-%m-%d %H:%M")}) + "\n")
            if errors % 10 == 0:
                print(f"  {errors} errors so far, last: {e}")
            time.sleep(2)

    progress["downloaded"] = downloaded + skipped
    save_progress(progress)
    print(f"\n  Download done: {downloaded} new, {skipped} existing, {errors} errors")

    # Retry any failed downloads
    error_log = DATA_DIR / "download_errors.jsonl"
    if error_log.exists() and errors > 0:
        print(f"\n  Retrying {errors} failed downloads...")
        retry_ids = set()
        for line in error_log.read_text().strip().split("\n"):
            try:
                entry = json.loads(line)
                retry_ids.add(entry["id"])
            except:
                pass

        retried = 0
        for did in retry_ids:
            pdf_path = PDF_DIR / f"{did}.pdf"
            if pdf_path.exists():
                continue
            url = f"{BASE_URL}/sudska-praksa/download/id/{did}/file/odluka"
            try:
                r = session.get(url, timeout=60)
                if r.status_code == 200:
                    pdf_path.write_bytes(r.content)
                    retried += 1
                time.sleep(1)
            except:
                pass
        print(f"  Retried: {retried} recovered")

    # Final verification
    total_ids = len(all_ids)
    total_pdfs = len(list(PDF_DIR.glob("*.pdf")))
    missing = total_ids - total_pdfs
    if missing > 0:
        print(f"\n  ⚠️  MISSING {missing} PDFs out of {total_ids} IDs!")
        print(f"  Run --download again to retry missing files.")
    else:
        print(f"\n  ✅ ALL {total_ids} PDFs downloaded!")


def extract_texts():
    """Phase 3: Extract text from ALL downloaded PDFs.

    Categories:
    - texts/       → successfully extracted (>50 chars text)
    - problematic/ → scanned/unreadable PDFs (saved for later OCR analysis)
    - empty/       → PDFs with no extractable content at all
    """
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    PROBLEM_DIR = DATA_DIR / "problematic"
    PROBLEM_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    extracted = 0
    problematic = 0
    already_done = 0

    print(f"  Extracting text from {len(pdf_files)} PDFs...")
    print(f"  Good texts → {TEXT_DIR}")
    print(f"  Problematic → {PROBLEM_DIR}")

    for pdf_path in pdf_files:
        text_path = TEXT_DIR / f"{pdf_path.stem}.json"
        problem_path = PROBLEM_DIR / f"{pdf_path.stem}.json"

        # Skip if already processed (in either folder)
        if text_path.exists() or problem_path.exists():
            already_done += 1
            continue

        try:
            pdf_bytes = pdf_path.read_bytes()
            text = extract_text_from_pdf(pdf_bytes)
            pdf_size = len(pdf_bytes)

            # Count pages
            page_count = 0
            try:
                import fitz
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                page_count = len(doc)
                doc.close()
            except:
                pass

            data = {
                "id": int(pdf_path.stem),
                "source_url": f"{BASE_URL}/sudska-praksa/download/id/{pdf_path.stem}/file/odluka",
                "full_text": text,
                "doc_type": "sudska_praksa",
                "pdf_size": pdf_size,
                "page_count": page_count,
                "text_length": len(text) if text else 0,
            }

            # Try to extract metadata from text
            if text:
                lines = text.split("\n")
                for line in lines[:30]:
                    line = line.strip()
                    if not line:
                        continue
                    if re.search(r'(?:Рев|Прев|Гж|Кж|Уж|Рж|Ку|Кзз|Узп)\s*\.?\s*\d+/\d+', line):
                        data["case_number"] = line
                    elif re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', line) and "date" not in data:
                        data["date"] = line
                    elif any(k in line for k in ["суд", "Суд", "СУД", "Sud", "SUD"]) and "court" not in data:
                        data["court"] = line

            # Categorize: good text vs problematic
            if text and len(text) > 50:
                text_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                extracted += 1
            else:
                # Problematic — scanned, image-based, or corrupted PDF
                data["problem"] = "scanned_or_empty"
                data["reason"] = f"Text too short ({len(text) if text else 0} chars) for {pdf_size} byte PDF with {page_count} pages"
                problem_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                problematic += 1

            if (extracted + problematic) % 200 == 0:
                print(f"  Processed {extracted + problematic + already_done}/{len(pdf_files)}: "
                      f"{extracted} good, {problematic} problematic, {already_done} already done")

        except Exception as e:
            # Save error info — never skip silently
            try:
                error_data = {
                    "id": int(pdf_path.stem),
                    "source_url": f"{BASE_URL}/sudska-praksa/download/id/{pdf_path.stem}/file/odluka",
                    "problem": "extraction_error",
                    "reason": str(e),
                    "pdf_size": pdf_path.stat().st_size,
                }
                problem_path.write_text(json.dumps(error_data, ensure_ascii=False, indent=2))
                problematic += 1
            except:
                problematic += 1

    progress = load_progress()
    progress["extracted"] = extracted
    progress["problematic"] = problematic
    save_progress(progress)
    print(f"\n  Extraction done:")
    print(f"    Good text:    {extracted}")
    print(f"    Problematic:  {problematic} (saved in {PROBLEM_DIR})")
    print(f"    Already done: {already_done}")
    print(f"    Total PDFs:   {len(pdf_files)}")


def ingest():
    """Phase 4: Ingest extracted texts into LexArdor ChromaDB."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from rag.store import get_collection

    text_files = sorted(TEXT_DIR.glob("*.json"))
    if not text_files:
        print("  No text files found. Run extraction first.")
        return

    collection = get_collection()
    initial = collection.count()
    print(f"  Ingesting {len(text_files)} court decisions (current corpus: {initial})...")

    batch_ids, batch_docs, batch_metas = [], [], []
    batch_size = 50
    ingested = 0

    for f in text_files:
        try:
            data = json.loads(f.read_text())
        except:
            continue

        text = data.get("full_text", "")
        if len(text) < 100:
            continue

        doc_id = f"court_{data['id']}"
        batch_ids.append(doc_id)
        batch_docs.append(text[:8000])
        batch_metas.append({
            "law_slug": doc_id,
            "law_title": data.get("case_number", f"Sudska odluka {data['id']}"),
            "doc_type": "sudska_praksa",
            "authority_level": 2,
            "gazette": data.get("case_number", ""),
            "article_number": "",
            "chapter": data.get("court", ""),
            "source_url": data.get("source_url", ""),
            "valid_from": data.get("date", ""),
        })

        if len(batch_ids) >= batch_size:
            try:
                collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                ingested += len(batch_ids)
                if ingested % 500 == 0:
                    print(f"  Ingested {ingested}/{len(text_files)}")
            except Exception as e:
                print(f"  Batch error: {e}")
            batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        try:
            collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            ingested += len(batch_ids)
        except Exception as e:
            print(f"  Final batch error: {e}")

    # Rebuild BM25 index
    print(f"  Rebuilding BM25 index...")
    try:
        from rag.bm25 import build_bm25_index
        build_bm25_index()
    except:
        pass

    print(f"\n  Ingested: {ingested} court decisions")
    print(f"  Corpus now: {collection.count()} (was {initial})")


def status():
    progress = load_progress()
    pdf_count = len(list(PDF_DIR.glob("*.pdf"))) if PDF_DIR.exists() else 0
    text_count = len(list(TEXT_DIR.glob("*.json"))) if TEXT_DIR.exists() else 0
    print(f"""
  ╔══════════════════════════════════════════╗
  ║  Court Decision Downloader — Status      ║
  ╠══════════════════════════════════════════╣
  ║  IDs collected:  {progress.get('total_ids', 0):>8}               ║
  ║  Last page:      {progress.get('last_page', 0):>8}               ║
  ║  PDFs downloaded: {pdf_count:>7}               ║
  ║  Texts extracted: {text_count:>7}               ║
  ║  Errors:         {len(progress.get('errors', [])):>8}               ║
  ╚══════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--captcha-url", help="Full URL with captcha token from browser")
    parser.add_argument("--phpsessid", help="PHPSESSID cookie value")
    parser.add_argument("--collect-ids", action="store_true", help="Phase 1: Collect decision IDs")
    parser.add_argument("--download", action="store_true", help="Phase 2: Download PDFs")
    parser.add_argument("--extract", action="store_true", help="Phase 3: Extract text from PDFs")
    parser.add_argument("--ingest", action="store_true", help="Phase 4: Ingest into LexArdor")
    parser.add_argument("--all", action="store_true", help="Run all phases")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-downloads", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="Resume from saved progress")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        status()
        return

    progress = load_progress()

    # Set credentials
    captcha_url = args.captcha_url or progress.get("captcha_url", "")
    phpsessid = args.phpsessid or progress.get("phpsessid", "")

    if not phpsessid:
        print("ERROR: Need --phpsessid. Get it from browser DevTools > Application > Cookies.")
        return

    if args.all or args.collect_ids:
        if not captcha_url:
            print("ERROR: Need --captcha-url for ID collection.")
            return
        collect_ids(captcha_url, phpsessid, max_pages=args.max_pages)

    if args.all or args.download:
        download_pdfs(phpsessid, max_downloads=args.max_downloads)

    if args.all or args.extract:
        extract_texts()

    if args.all or args.ingest:
        ingest()

    if args.resume:
        if progress.get("all_ids"):
            download_pdfs(phpsessid, max_downloads=args.max_downloads)
            extract_texts()
        elif captcha_url:
            collect_ids(captcha_url, phpsessid, max_pages=args.max_pages)

    status()


if __name__ == "__main__":
    main()
