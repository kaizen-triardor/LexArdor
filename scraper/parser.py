"""Parse raw Serbian law text into structured articles (Član).

Enhanced for Legal Expert Engine: extracts sub-articles (stav/tačka),
cross-references, gazette version history, document type classification,
and authority level.
"""

import re
from typing import Optional


def slugify(text: str) -> str:
    """Create URL-friendly slug from law title."""
    text = text.lower().strip()
    replacements = {
        "č": "c", "ć": "c", "đ": "dj", "š": "s", "ž": "z",
        "Č": "C", "Ć": "C", "Đ": "Dj", "Š": "S", "Ž": "Z",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


# ── Document type classification ─────────────────────────────────────────────

def classify_document_type(title: str, slug: str = "") -> tuple[str, int]:
    """Classify a legal document by type and authority level.

    Returns (doc_type, authority_level) where authority_level:
        1 = Ustav (Constitution)
        2 = Zakon/Zakonik (Law/Code)
        3 = Uredba/Kolektivni ugovor (Decree/Collective agreement)
        4 = Pravilnik/Odluka (Regulation/Decision)
        5 = Mišljenje/Ostalo (Opinion/Other)
    """
    t = (title + " " + slug).lower()
    # Normalize: replace underscores/hyphens with spaces for word boundary matching
    t = t.replace("_", " ").replace("-", " ")
    # Remove gazette refs that might contain confusing words
    t = re.sub(r'\(?"sl\.\s*glasnik[^)]*\)?', '', t)

    if re.search(r'\bustav\b', t) and not re.search(r'\bustavni[\s-]?zakon\b', t):
        return ("ustav", 1)
    if re.search(r'\bzakonik\b', t):
        return ("zakonik", 2)
    if re.search(r'\bzakon\b', t):
        return ("zakon", 2)
    if re.search(r'\buredba\b', t):
        return ("uredba", 3)
    if re.search(r'\bkolektivni[\s-]?ugovor\b', t):
        return ("kolektivni_ugovor", 3)
    if re.search(r'\bpravilnik\b', t):
        return ("pravilnik", 4)
    if re.search(r'\bodluka\b', t):
        return ("odluka", 4)
    if re.search(r'\bnaredba\b', t):
        return ("naredba", 4)
    if re.search(r'\bautenticno[\s-]?tumacenje\b', t):
        return ("autenticno_tumacenje", 4)
    if re.search(r'\bmisljenje\b|\bmišljenje\b', t):
        return ("misljenje", 5)
    return ("ostalo", 5)


# ── Gazette reference parsing ────────────────────────────────────────────────

def parse_gazette_refs(gazette: str) -> list[dict]:
    """Parse gazette string into structured version list.

    Input:  '"Sl. glasnik RS", br. 85/2005, 88/2005 - ispr., 107/2005 - ispr., 72/2009'
    Output: [{"number": "85/2005", "year": 2005, "issue": 85, "change_type": "original", "note": ""}, ...]
    """
    if not gazette:
        return []

    results = []
    # Extract the part after "br." or just find all N/YYYY patterns
    # Remove the prefix: "Sl. glasnik RS", br.
    cleaned = re.sub(r'^[^0-9]*(?:br\.?\s*)?', '', gazette.strip(' "\'()'))

    # Split on commas, but keep notes attached
    parts = re.split(r',\s*', cleaned)

    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue

        # Match: number/year possibly followed by note
        m = re.match(r'(\d+)\s*/\s*(\d{4})\s*(.*)', part)
        if not m:
            # Try just year-like pattern
            m2 = re.match(r'(\d+)\s*/\s*(\d{2,4})', part)
            if m2:
                issue = int(m2.group(1))
                year = int(m2.group(2))
                if year < 100:
                    year += 2000
                results.append({
                    "number": f"{issue}/{year}",
                    "year": year,
                    "issue": issue,
                    "change_type": "original" if i == 0 else "amendment",
                    "note": "",
                })
            continue

        issue = int(m.group(1))
        year = int(m.group(2))
        note_raw = m.group(3).strip(' -–—').strip()

        # Classify change type from note
        change_type = "original" if i == 0 else "amendment"
        note = note_raw

        if note_raw:
            lower_note = note_raw.lower()
            if "ispr" in lower_note:
                change_type = "correction"
            elif "dr. zakon" in lower_note or "drugi zakon" in lower_note:
                change_type = "other_law"
            elif "usklad" in lower_note or "din. izn" in lower_note:
                change_type = "adjustment"
            elif "odluka" in lower_note and "us" in lower_note:
                change_type = "constitutional_court"

        results.append({
            "number": f"{issue}/{year}",
            "year": year,
            "issue": issue,
            "change_type": change_type,
            "note": note,
        })

    return results


def derive_valid_from(gazette_refs: list[dict]) -> str | None:
    """Derive approximate valid_from date from first gazette reference."""
    if not gazette_refs:
        return None
    first = gazette_refs[0]
    year = first.get("year")
    if year:
        return f"{year}-01-01"
    return None


# ── Sub-article extraction (stav/tačka) ─────────────────────────────────────

def extract_sub_articles(article_text: str) -> list[dict]:
    """Parse stav (paragraph) and tačka (point) structure from article text.

    Serbian legal convention:
    - Stav: separate paragraphs, sometimes explicitly numbered
    - Tačka: numbered points within a stav, formatted as "1)", "2)", "3)"

    Returns list of: {"stav": int|None, "tacka": int|None, "text": str}
    """
    if not article_text or len(article_text) < 10:
        return []

    results = []
    # Split into paragraphs (double newline or significant whitespace)
    paragraphs = re.split(r'\n\s*\n|\n(?=\s{2,})', article_text)
    if len(paragraphs) <= 1:
        # Try splitting on single newlines if text has them
        paragraphs = [p.strip() for p in article_text.split('\n') if p.strip()]

    stav_num = 0
    for para in paragraphs:
        para = para.strip()
        if not para or len(para) < 5:
            continue

        stav_num += 1

        # Check for tačke within this stav: "1) text", "2) text"
        tacka_pattern = re.compile(r'(?:^|\n)\s*(\d+)\)\s+(.*?)(?=\n\s*\d+\)|$)', re.DOTALL)
        tacke = list(tacka_pattern.finditer(para))

        if tacke:
            # There's text before the first tačka (the stav intro)
            intro_end = tacke[0].start()
            intro = para[:intro_end].strip()
            if intro:
                results.append({"stav": stav_num, "tacka": None, "text": intro})

            for tm in tacke:
                tacka_num = int(tm.group(1))
                tacka_text = tm.group(2).strip()
                results.append({"stav": stav_num, "tacka": tacka_num, "text": tacka_text})
        else:
            results.append({"stav": stav_num, "tacka": None, "text": para})

    return results


# ── Cross-reference extraction ───────────────────────────────────────────────

def extract_cross_references(article_text: str, own_law_slug: str = "") -> list[dict]:
    """Extract cross-references to other articles/laws from article text.

    Patterns detected:
    - "član N" / "čl. N" / "člana N" / "članom N" (internal/external article ref)
    - "stav N" / "stava N" (sub-article ref)
    - "tačka N)" / "tačke N)" (point ref)
    - "Zakona o ..." (external law ref)
    - "ovog zakona" (internal self-reference marker)

    Returns list of: {
        "target_article": str,
        "target_stav": int|None,
        "target_tacka": int|None,
        "target_law_slug": str,  # own_law_slug if internal, or derived slug
        "ref_type": "internal"|"external",
        "citation_text": str,
    }
    """
    if not article_text:
        return []

    results = []
    seen = set()

    # Pattern: član/čl./člana/članom N (optionally with stav and tačka)
    ref_pattern = re.compile(
        r'(?:član[auo]?m?|čl\.)\s+(\d+[a-z]?)'
        r'(?:\.\s*(?:st(?:av)?\.?\s*(\d+))?)?'
        r'(?:\s*(?:tačk[aei]\.?\s*(\d+)\)?))?'
        r'(.*?)(?:\.|,|\n|$)',
        re.IGNORECASE
    )

    for m in ref_pattern.finditer(article_text):
        target_art = m.group(1)
        target_stav = int(m.group(2)) if m.group(2) else None
        target_tacka = int(m.group(3)) if m.group(3) else None
        context = m.group(4).strip() if m.group(4) else ""

        # Determine if internal or external
        ref_type = "internal"
        target_law = own_law_slug

        # Check if context mentions another law
        law_match = re.search(r'(?:Zakon[a-z]*)\s+o\s+([^,\.\n]{3,60})', context, re.IGNORECASE)
        if law_match:
            ref_type = "external"
            target_law = slugify(law_match.group(0))
        elif "ovog zakona" not in context.lower() and not context:
            # Ambiguous — could be internal or external
            ref_type = "internal"

        key = (target_art, target_stav, target_tacka, target_law)
        if key not in seen:
            seen.add(key)
            results.append({
                "target_article": target_art,
                "target_stav": target_stav,
                "target_tacka": target_tacka,
                "target_law_slug": target_law,
                "ref_type": ref_type,
                "citation_text": m.group(0).strip()[:200],
            })

    return results


def extract_gazette(text: str) -> Optional[str]:
    """Extract Službeni glasnik reference from text."""
    # Match patterns like ("Sl. glasnik RS", br. 18/2020, 6/2023 - ...)
    pattern = r'\("Sl\.\s*glasnik\s+RS"[^)]*\)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(0)
    # Also try without parens
    pattern2 = r'"Sl\.\s*glasnik\s+RS"[^"\n]*'
    match2 = re.search(pattern2, text, re.IGNORECASE)
    if match2:
        return match2.group(0)
    return None


def extract_title(text: str) -> str:
    """Extract the law title from the first meaningful lines."""
    lines = text.strip().split("\n")
    title_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if title_lines:
                break
            continue
        # Stop at gazette reference or first article
        if re.match(r'\(?"Sl\.', stripped, re.IGNORECASE):
            break
        if re.match(r"Član\s+\d+", stripped, re.IGNORECASE):
            break
        title_lines.append(stripped)
    return " ".join(title_lines) if title_lines else "Nepoznat zakon"


def parse_law_text(text: str, existing_slug: str = None) -> dict:
    """Parse raw law text into structured articles with enhanced metadata.

    Returns dict with:
        slug, title, gazette, article_count, articles[],
        doc_type, authority_level, gazette_refs[], valid_from
    """
    title = extract_title(text)
    gazette = extract_gazette(text)
    slug = existing_slug or slugify(title)

    # Classify document
    doc_type, authority_level = classify_document_type(title, slug)

    # Parse gazette versions
    gazette_refs = parse_gazette_refs(gazette or "")
    valid_from = derive_valid_from(gazette_refs)
    latest_gazette = gazette_refs[-1]["number"] if gazette_refs else ""
    gazette_numbers = [g["number"] for g in gazette_refs]

    # Track current chapter/section
    chapter_pattern = re.compile(
        r"^(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3}))\.?\s+(.+)$",
        re.MULTILINE,
    )

    article_pattern = re.compile(
        r"(?:^|\n)\s*Član\s+(\d+[a-z]?)\.?\s*\n",
        re.IGNORECASE,
    )

    chapters = []
    for m in chapter_pattern.finditer(text):
        chapters.append((m.start(), m.group(1), m.group(2).strip()))

    def get_chapter_at(pos: int) -> tuple[str, str]:
        """Returns (chapter_number, chapter_title) for position."""
        current_num, current_title = "", ""
        for ch_pos, ch_num, ch_title in chapters:
            if ch_pos <= pos:
                current_num = ch_num
                current_title = ch_num + ". " + ch_title
            else:
                break
        return current_num, current_title

    splits = list(article_pattern.finditer(text))
    articles = []

    for i, match in enumerate(splits):
        number = match.group(1)
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        article_text = text[start:end].strip()
        chapter_num, chapter_title = get_chapter_at(match.start())

        # Extract sub-articles (stav/tačka)
        sub_articles = extract_sub_articles(article_text)
        stav_count = len(set(s["stav"] for s in sub_articles if s["stav"]))
        tacka_count = len([s for s in sub_articles if s["tacka"]])

        # Extract cross-references
        cross_references = extract_cross_references(article_text, slug)

        articles.append({
            "number": number,
            "text": article_text,
            "chapter": chapter_title or None,
            "chapter_number": chapter_num,
            "sub_articles": sub_articles,
            "cross_references": cross_references,
            "stav_count": stav_count,
            "tacka_count": tacka_count,
        })

    return {
        "slug": slug,
        "title": title,
        "gazette": gazette,
        "doc_type": doc_type,
        "authority_level": authority_level,
        "gazette_refs": gazette_refs,
        "gazette_numbers": gazette_numbers,
        "latest_gazette": latest_gazette,
        "valid_from": valid_from,
        "article_count": len(articles),
        "articles": articles,
    }
