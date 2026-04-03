"""Fast ID collector — minimal overhead, maximum speed.

Usage:
    python scraper/fast_collect.py YEAR PHPSESSID "CAPTCHA_URL"
"""
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


def main():
    if len(sys.argv) < 4:
        print("Usage: python scraper/fast_collect.py YEAR PHPSESSID \"CAPTCHA_URL\"")
        sys.exit(1)

    year = int(sys.argv[1])
    phpsessid = sys.argv[2]
    captcha_url = sys.argv[3]

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Parse URL params
    parsed = urlparse(captcha_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
    flat["godina"] = str(year)

    # Separate captcha params
    captcha_keys = {"g-recaptcha-response", "captcha", "Submit"}
    captcha_params = {k: flat.pop(k) for k in captcha_keys if k in flat}

    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="sudskapraksa.sud.rs")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/sudska-praksa",
    })

    # Load existing
    master_file = DATA_DIR / "_all_ids.json"
    if master_file.exists():
        master = json.loads(master_file.read_text())
    else:
        master = {"all_ids": [], "by_year": {}, "total": 0}

    existing = set(master["all_ids"])
    year_ids = set(master.get("by_year", {}).get(str(year), []))
    new_total = 0

    print(f"Year {year}: have {len(year_ids)}, scraping...")

    page = 1
    empty = 0
    while empty < 5:
        rp = {**flat, "page": str(page)}
        if page == 1:
            rp.update(captcha_params)

        try:
            r = session.get(f"{BASE_URL}/sudska-praksa", params=rp, timeout=20)
            if r.status_code != 200:
                empty += 1
                page += 1
                time.sleep(0.5)
                continue

            links = re.findall(r'/sudska-praksa/download/id/(\d+)/file/odluka', r.text)
            if not links:
                empty += 1
                page += 1
                time.sleep(0.3)
                continue

            empty = 0
            new_on_page = 0
            for did_str in links:
                did = int(did_str)
                if did not in existing:
                    existing.add(did)
                    year_ids.add(did)
                    new_on_page += 1
                    new_total += 1

            print(f"  p{page}: +{new_on_page} (year={len(year_ids)})")
            page += 1
            time.sleep(0.4)

        except KeyboardInterrupt:
            print(f"\nInterrupted at page {page}")
            break
        except Exception as e:
            print(f"  p{page} err: {e}")
            page += 1
            time.sleep(1)

    # Save
    master["all_ids"] = sorted(existing)
    master["by_year"][str(year)] = sorted(year_ids)
    master["total"] = len(master["all_ids"])
    master_file.write_text(json.dumps(master, indent=2))

    yp = DATA_DIR / f"_year_{year}_progress.json"
    yp.write_text(json.dumps({"year": year, "last_page": page, "ids": sorted(year_ids), "total": len(year_ids)}))

    print(f"\nDone: {year} = {len(year_ids)} IDs (+{new_total} new)")


if __name__ == "__main__":
    main()
