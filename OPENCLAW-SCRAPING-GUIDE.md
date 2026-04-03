# OpenClaw Scraping Guide — Serbian Laws for LexArdor

## Mission

Scrape ALL Serbian laws from **paragraf.rs** and save them as structured JSON files into:

```
/home/kaizenlinux/Projects/Project_02/lexardor-v2/data/laws/
```

Each law gets its own `.json` file. After scraping, we'll ingest them into our ChromaDB vector database for the LexArdor AI legal assistant.

---

## Source: paragraf.rs

**Main index page:** https://www.paragraf.rs/propisi.html

This page lists 500+ Serbian laws and regulations as links. Each link points to a full-text law page.

**Structure:**
- The index page is a single flat HTML page (no pagination)
- Laws are listed as `<a>` links with href pattern: `propisi/slug-name.html`
- Each link text contains the law title in ALL CAPS and the Službeni glasnik reference in parentheses

**Individual law pages:** `https://www.paragraf.rs/propisi/{slug}.html`
- Full text of the law with articles marked as "Član N." (Article N.)
- Chapters marked with Roman numerals (I, II, III...) and section titles
- Gazette reference at the top: `("Sl. glasnik RS", br. XX/YYYY, ...)`

---

## What to Scrape

### Phase 1: Priority Laws (TOP 20 — do these first)

These are the most important Serbian laws that lawyers use daily:

| # | Law Name (Serbian) | Expected Slug | Category |
|---|-------------------|---------------|----------|
| 1 | Zakon o obligacionim odnosima | zakon-o-obligacionim-odnosima | Contract/Civil |
| 2 | Zakon o radu | zakon-o-radu | Labor |
| 3 | Zakon o parničnom postupku | zakon-o-parnicnom-postupku | Civil Procedure |
| 4 | Krivični zakonik | krivicni-zakonik | Criminal |
| 5 | Zakonik o krivičnom postupku | zakonik-o-krivicnom-postupku | Criminal Procedure |
| 6 | Zakon o privrednim društvima | zakon-o-privrednim-drustvima | Company |
| 7 | Zakon o opštem upravnom postupku | zakon-o-opstem-upravnom-postupku | Administrative |
| 8 | Porodični zakon | porodicni-zakon | Family |
| 9 | Zakon o nasleđivanju | zakon-o-nasledjivanju | Inheritance |
| 10 | Zakon o zaštiti potrošača | zakon-o-zastiti-potrosaca | Consumer |
| 11 | Zakon o zaštiti podataka o ličnosti | zakon-o-zastiti-podataka-o-licnosti | Data Protection |
| 12 | Zakon o javnim nabavkama | zakon-o-javnim-nabavkama | Procurement |
| 13 | Zakon o stečaju | zakon-o-stecaju | Bankruptcy |
| 14 | Zakon o izvršenju i obezbeđenju | zakon-o-izvrsenju-i-obezbedenju | Enforcement |
| 15 | Zakon o advokaturi | zakon-o-advokaturi | Advocacy |
| 16 | Zakon o autorskom pravu | zakon-o-autorskom-pravu | Copyright |
| 17 | Zakon o planiranju i izgradnji | zakon-o-planiranju-i-izgradnji | Construction |
| 18 | Zakon o porezu na dohodak građana | zakon-o-porezu-na-dohodak-gradjana | Tax |
| 19 | Zakon o PDV-u | zakon-o-pdv | VAT |
| 20 | Zakon o osnovama svojinskopravnih odnosa | zakon-o-osnovama-svojinskopravnih-odnosa | Property |

### Phase 2: ALL remaining laws

After Phase 1 is done, scrape every remaining law from the index page at https://www.paragraf.rs/propisi.html

---

## How to Scrape Each Law

### Step 1: Go to the law page

Navigate to `https://www.paragraf.rs/propisi/{slug}.html`

### Step 2: Extract the full text

Copy the ENTIRE law text from the page — everything from the title down to the last article. Include:
- The title (usually in ALL CAPS at the top)
- The Službeni glasnik reference: `("Sl. glasnik RS", br. XX/YYYY, ...)`
- ALL chapters, sections, and articles
- Every "Član N." (Article) with its full text

### Step 3: Save as JSON

Save each law as a JSON file in `/home/kaizenlinux/Projects/Project_02/lexardor-v2/data/laws/`

**Filename:** `{slug}.json`

**JSON format:**

```json
{
  "slug": "zakon-o-radu",
  "title": "ZAKON O RADU",
  "gazette": "Sl. glasnik RS, br. 24/2005, 61/2005, 54/2009, 32/2013, 75/2014, 13/2017, 113/2017, 95/2018",
  "source_url": "https://www.paragraf.rs/propisi/zakon-o-radu.html",
  "scraped_at": "2026-03-22T12:00:00",
  "article_count": 287,
  "articles": [
    {
      "number": "1",
      "text": "Prava, obaveze i odgovornosti iz radnog odnosa, odnosno po osnovu rada, uređuju se ovim zakonom i posebnim zakonom, u skladu sa ratifikovanim međunarodnim konvencijama.",
      "chapter": "I. OSNOVNE ODREDBE"
    },
    {
      "number": "2",
      "text": "Odredbe ovog zakona primenjuju se na zaposlene koji rade na teritoriji Republike Srbije, kod domaćeg ili stranog pravnog, odnosno fizičkog lica...",
      "chapter": "I. OSNOVNE ODREDBE"
    },
    {
      "number": "24",
      "text": "Radni odnos zasniva se ugovorom o radu.",
      "chapter": "II. ZASNIVANJE RADNOG ODNOSA"
    }
  ]
}
```

### Field rules:

| Field | Description | Example |
|-------|-------------|---------|
| `slug` | URL-friendly name, lowercase, hyphens | `zakon-o-radu` |
| `title` | Law title exactly as it appears on page (ALL CAPS) | `ZAKON O RADU` |
| `gazette` | Službeni glasnik reference without outer parentheses | `Sl. glasnik RS, br. 24/2005...` |
| `source_url` | Full URL of the law page | `https://www.paragraf.rs/propisi/zakon-o-radu.html` |
| `scraped_at` | ISO timestamp of when you scraped it | `2026-03-22T12:00:00` |
| `article_count` | Total number of articles extracted | `287` |
| `articles` | Array of article objects | see below |
| `articles[].number` | Article number (string — can be "24a") | `"1"`, `"24a"` |
| `articles[].text` | Full text of the article (may be multi-paragraph) | The complete article body |
| `articles[].chapter` | Current chapter/section the article belongs to | `"I. OSNOVNE ODREDBE"` |

---

## How to Parse Articles

Serbian laws follow a consistent structure:

```
ZAKON O RADU
("Sl. glasnik RS", br. 24/2005, ...)

I. OSNOVNE ODREDBE

Član 1.
[article text here]

Član 2.
[article text here]

II. ZASNIVANJE RADNOG ODNOSA

Član 24.
[article text here]
```

**Rules:**
1. Articles start with `Član N.` (or `Član Na.` for sub-articles like 24a)
2. Chapter headers are lines with Roman numerals: `I.`, `II.`, `III.`, etc. followed by ALL CAPS title
3. Everything between one `Član` and the next belongs to that article
4. Keep the chapter context — track which chapter each article belongs to
5. Some articles have sub-paragraphs (stav) — include the full text including all sub-paragraphs

---

## Output Directory Structure

```
/home/kaizenlinux/Projects/Project_02/lexardor-v2/data/laws/
├── zakon-o-obligacionim-odnosima.json
├── zakon-o-radu.json
├── zakon-o-parnicnom-postupku.json
├── krivicni-zakonik.json
├── zakonik-o-krivicnom-postupku.json
├── zakon-o-privrednim-drustvima.json
├── zakon-o-opstem-upravnom-postupku.json
├── porodicni-zakon.json
├── zakon-o-nasledjivanju.json
├── zakon-o-zastiti-potrosaca.json
├── zakon-o-zastiti-podataka-o-licnosti.json
├── zakon-o-javnim-nabavkama.json
├── zakon-o-stecaju.json
├── zakon-o-izvrsenju-i-obezbedenju.json
├── zakon-o-advokaturi.json
├── zakon-o-autorskom-pravu.json
├── zakon-o-planiranju-i-izgradnji.json
├── zakon-o-porezu-na-dohodak-gradjana.json
├── zakon-o-pdv.json
├── zakon-o-osnovama-svojinskopravnih-odnosa.json
└── ... (all remaining laws from paragraf.rs)
```

---

## Important Notes

1. **Be respectful:** Wait 2-3 seconds between page requests. paragraf.rs is a public legal resource — don't hammer it.

2. **Encoding:** Save all JSON files as UTF-8. Serbian text includes characters like: č, ć, š, ž, đ (Latin) and full Cyrillic alphabet.

3. **Store in Latin script:** Most laws on paragraf.rs are in Latin script. Store them as-is. Our system handles Cyrillic conversion internally.

4. **Some laws are very large:** Zakon o obligacionim odnosima has 1000+ articles. That's fine — save the full thing.

5. **Versioned laws:** Some slugs include years (e.g., `krivicni-zakonik-2019`). Scrape the most current version.

6. **Skip non-law pages:** The index may include links to regulations (uredbe), rules (pravilnici), etc. Scrape them too — they're all useful legal text.

---

## After Scraping Is Done

Once all JSON files are in `data/laws/`, run this to ingest them into the LexArdor vector database:

```bash
cd /home/kaizenlinux/Projects/Project_02/lexardor-v2
python3 scripts/ingest_laws.py
```

This will embed all articles into ChromaDB and make them searchable by the AI assistant.

---

## Verification

After scraping, the files should:
- Be valid JSON (parseable by `python3 -m json.tool filename.json`)
- Have at least 1 article per file
- Have the `slug` field matching the filename (without .json)
- Total across all files: expect 50,000 - 200,000 articles
