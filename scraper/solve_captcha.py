"""Quick helper: opens browser, you solve captcha, it saves cookies for the scraper."""
from playwright.sync_api import sync_playwright
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

print("\n  Opening browser at sudskapraksa.sud.rs...")
print("  1. Klikni reCAPTCHA checkbox")
print("  2. Reši sliku ako treba")
print("  3. Klikni ПРЕТРАГА dugme")
print("  4. Sačekaj da se rezultati prikažu")
print("  5. Vrati se ovde i pritisni ENTER\n")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://sudskapraksa.sud.rs/sudska-praksa")

    input("  >>> Reši captcha i klikni Pretraga, pa ENTER ovde... ")

    # Save cookies
    cookies = {c["name"]: c["value"] for c in ctx.cookies()}
    url = page.url
    content = page.content()

    # Save for scraper
    progress = {"cookies": cookies, "last_page": 0, "total_scraped": 0, "total_expected": 0, "errors": []}
    (DATA_DIR / "_progress.json").write_text(json.dumps(progress, indent=2))
    (DATA_DIR / "_first_page.html").write_text(content)

    print(f"\n  Cookies saved! URL: {url[:80]}")
    print(f"  PHPSESSID: {cookies.get('PHPSESSID', 'N/A')}")
    print(f"  First page saved: {len(content)} bytes")
    print(f"\n  Sad pokreni: python scraper/court_scraper.py")

    browser.close()
