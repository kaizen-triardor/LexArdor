"""Simple captcha solver — opens Windows browser, you solve captcha, paste cookies back."""
import json
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

print("""
╔══════════════════════════════════════════════════════════╗
║  LexArdor — Captcha Solver za sudskapraksa.sud.rs       ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  1. Otvaramo sajt u tvom Windows browser-u               ║
║  2. Reši reCAPTCHA i klikni Pretragа                     ║
║  3. Kada se rezultati prikažu, otvori DevTools:          ║
║     - Pritisni F12                                       ║
║     - Idi na tab "Application" (ili "Storage")           ║
║     - Klikni "Cookies" > sudskapraksa.sud.rs             ║
║     - Kopiraj vrednost PHPSESSID cookie-a                ║
║  4. Vrati se ovde i nalepi PHPSESSID                     ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""")

# Open in Windows browser
url = "https://sudskapraksa.sud.rs/sudska-praksa"
try:
    # WSL: open in Windows browser
    subprocess.run(["wslview", url], check=False, capture_output=True)
    print(f"  Browser otvoren: {url}")
except:
    try:
        subprocess.run(["xdg-open", url], check=False, capture_output=True)
        print(f"  Browser otvoren: {url}")
    except:
        print(f"  Otvori ručno u browser-u: {url}")

print()
phpsessid = input("  Nalepi PHPSESSID cookie ovde: ").strip()

if not phpsessid:
    print("  ERROR: PHPSESSID je prazan!")
    sys.exit(1)

# Save cookies
cookies = {"PHPSESSID": phpsessid}
progress = {
    "cookies": cookies,
    "last_page": 0,
    "total_scraped": 0,
    "total_expected": 0,
    "errors": [],
}

progress_file = DATA_DIR / "_progress.json"
progress_file.write_text(json.dumps(progress, indent=2, ensure_ascii=False))

print(f"\n  ✅ PHPSESSID sačuvan: {phpsessid[:20]}...")
print(f"  ✅ Progress fajl: {progress_file}")
print(f"\n  Sada pokreni scraper:")
print(f"  ! python ~/Projects/Project_02_LEXARDOR/lexardor-v2/scraper/court_full_scraper.py")
