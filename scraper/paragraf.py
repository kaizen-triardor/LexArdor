"""Scrape Serbian laws from paragraf.rs using Scrapling."""

import json
import time
import logging
from pathlib import Path
from typing import Optional

from scraper.parser import parse_law_text

logger = logging.getLogger(__name__)

BASE_URL = "https://www.paragraf.rs"
INDEX_URL = f"{BASE_URL}/propisi.html"

# Top 20 priority Serbian laws to scrape first
PRIORITY_SLUGS = [
    "zakon_o_radu",
    "zakon_o_obligacionim_odnosima",
    "krivicni_zakonik",
    "zakon_o_krivicnom_postupku",
    "zakon_o_parnicnom_postupku",
    "zakon_o_upravnom_postupku",
    "zakon_o_privrednim_drustvima",
    "porodicni_zakon",
    "zakon_o_nasledjivanju",
    "zakon_o_svojini",
    "zakon_o_planiranju_i_izgradnji",
    "zakon_o_porezu_na_dodatu_vrednost",
    "zakon_o_porezu_na_dohodak_gradjana",
    "zakon_o_javnim_nabavkama",
    "zakon_o_zastiti_potrosaca",
    "zakon_o_izvrsenju_i_obezbedjenju",
    "zakon_o_stecaju",
    "zakon_o_zastiti_podataka_o_licnosti",
    "zakon_o_elektronskim_komunikacijama",
    "zakon_o_osnovama_sistema_obrazovanja_i_vaspitanja",
]


def fetch_law_index() -> list[dict]:
    """Scrape the paragraf.rs index page for all law links.

    Returns list of dicts: [{slug, title, url}, ...]
    """
    from scrapling.fetchers import StealthyFetcher

    logger.info("Fetching law index from %s", INDEX_URL)
    page = StealthyFetcher.fetch(INDEX_URL, headless=True)

    laws = []
    links = page.css('a[href*="/propisi/"]')

    for link in links:
        href = link.attrib.get("href", "")
        title = link.text_content().strip()
        if not href or not title:
            continue

        # Extract slug from URL: /propisi/slug.html -> slug
        slug = href.split("/propisi/")[-1].replace(".html", "").strip("/")
        if not slug:
            continue

        # Build full URL if relative
        if href.startswith("/"):
            url = BASE_URL + href
        elif href.startswith("http"):
            url = href
        else:
            url = f"{BASE_URL}/propisi/{href}"

        laws.append({
            "slug": slug,
            "title": title,
            "url": url,
        })

    logger.info("Found %d laws on index page", len(laws))
    return laws


def fetch_law_text(url: str) -> str:
    """Fetch the full text of a single law page.

    Args:
        url: Full URL of the law page on paragraf.rs

    Returns:
        Full text content of the law page
    """
    from scrapling.fetchers import StealthyFetcher

    logger.info("Fetching law text from %s", url)
    page = StealthyFetcher.fetch(url, headless=True)

    # paragraf.rs puts law content in a main content div
    # Try common content selectors
    for selector in [".propis-text", ".law-content", "#law-text", ".content", "article", "main"]:
        elements = page.css(selector)
        if elements:
            return elements[0].text_content().strip()

    # Fallback: get all text from body
    body = page.css("body")
    if body:
        return body[0].text_content().strip()

    return ""


def _match_slug(slug: str, available: list[dict]) -> Optional[dict]:
    """Match a slug against available laws with partial matching.

    Args:
        slug: The slug to match (can be partial)
        available: List of law dicts from fetch_law_index()

    Returns:
        Matched law dict or None
    """
    # Exact match first
    for law in available:
        if law["slug"] == slug:
            return law

    # Partial match: slug is contained in available slug
    for law in available:
        if slug in law["slug"]:
            return law

    # Partial match: available slug is contained in target slug
    for law in available:
        if law["slug"] in slug:
            return law

    return None


def scrape_laws(
    slugs: list[str],
    output_dir: str,
    delay: float = 3.0,
) -> list[str]:
    """Scrape and save laws as JSON files.

    Args:
        slugs: List of law slugs to scrape
        output_dir: Directory to save JSON files
        delay: Delay between requests in seconds (default 3.0)

    Returns:
        List of file paths for successfully saved laws
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching law index...")
    available = fetch_law_index()
    if not available:
        logger.error("No laws found on index page")
        return []

    saved_files = []

    for slug in slugs:
        matched = _match_slug(slug, available)
        if not matched:
            logger.warning("No match found for slug: %s", slug)
            continue

        logger.info("Scraping: %s (%s)", matched["title"], matched["slug"])

        try:
            raw_text = fetch_law_text(matched["url"])
            if not raw_text:
                logger.warning("Empty text for %s", matched["slug"])
                continue

            parsed = parse_law_text(raw_text)
            parsed["source_url"] = matched["url"]
            parsed["source_slug"] = matched["slug"]

            out_file = output_path / f"{matched['slug']}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(parsed, f, ensure_ascii=False, indent=2)

            saved_files.append(str(out_file))
            logger.info(
                "Saved %s (%d articles) -> %s",
                matched["slug"],
                parsed["article_count"],
                out_file,
            )

        except Exception as e:
            logger.exception("Failed to scrape %s: %s", matched["slug"], e)

        # Be respectful: delay between requests
        if slug != slugs[-1]:
            logger.debug("Waiting %.1f seconds...", delay)
            time.sleep(delay)

    logger.info("Done. Saved %d/%d laws.", len(saved_files), len(slugs))
    return saved_files
