# LexArdor — Šabloni dokumenata (Document Templates)

**Date:** 2026-03-22
**Feature:** AI-assisted document filling from learned templates

---

## Concept

Lawyers work with the same document types repeatedly — tužbe, ugovori, rešenja, punomoćja.
Each has a fixed structure but different client data.

**The workflow:**
1. Lawyer creates a **šablon** (template) — uploads a filled example document
2. AI analyzes the example and identifies **variable fields** (names, dates, amounts, addresses)
3. Next time — lawyer says "popuni tužbu za klijenta X" and fills in just the variables
4. AI generates the completed document, checks for consistency, flags missing info
5. Lawyer reviews, edits, exports as PDF or DOCX

---

## User Flow (Step by Step)

### Creating a Template

```
1. Klik: [+ Novi šablon]
2. Modal:
   - Naziv šablona: "Tužba za naknadu štete"
   - Tip: [Tužba ▾] (Tužba, Ugovor, Rešenje, Punomoćje, Dopis, Ostalo)
   - Primer dokumenta: [paste text or upload DOCX]
3. AI analyzes and extracts fields:
   "Pronašao sam 8 promenljivih polja:"
   - {{ime_tužioca}} = "Petar Petrović"
   - {{jmbg_tužioca}} = "0101990710123"
   - {{adresa_tužioca}} = "Kneza Miloša 45, Beograd"
   - {{ime_tuženog}} = "ABC d.o.o."
   - {{iznos}} = "150.000,00 RSD"
   - {{datum}} = "15.03.2026"
   - {{sud}} = "Osnovni sud u Beogradu"
   - {{opis_spora}} = "naknada štete iz ugovora o..."
4. Lawyer confirms/edits field names
5. Template saved with extracted structure
```

### Filling a Document from Template

```
1. Select template: "Tužba za naknadu štete"
2. Form appears with all variable fields:
   - Ime tužioca: [____________]
   - JMBG tužioca: [____________]
   - Adresa tužioca: [____________]
   - ... etc
3. Optional: "AI popuni" button — paste case description,
   AI extracts values from free text
4. Preview generated document (live preview as you type)
5. AI checks:
   - ✅ Sva obavezna polja popunjena
   - ⚠️ JMBG format neispravan (12 cifara umesto 13)
   - ✅ Datum u ispravnom formatu
6. [Izvezi kao PDF] [Izvezi kao DOCX] [Sačuvaj draft]
```

---

## Data Model

### Template (stored in SQLite)
```sql
CREATE TABLE templates (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    name TEXT NOT NULL,
    doc_type TEXT,           -- tuzba, ugovor, resenje, punomocje, dopis, ostalo
    body_template TEXT,      -- full text with {{field}} placeholders
    fields TEXT,             -- JSON: [{name, label, type, required, example, validation}]
    example_values TEXT,     -- JSON: {field_name: example_value}
    created_at TEXT,
    updated_at TEXT
);
```

### Field types
- `text` — free text (name, description)
- `jmbg` — 13 digits, validated
- `pib` — 9 digits, validated
- `date` — DD.MM.YYYY format
- `money` — number with currency
- `address` — street + number + city
- `phone` — phone number
- `email` — email address
- `multiline` — long text (opis spora, obrazloženje)

### Draft (saved partially-filled documents)
```sql
CREATE TABLE drafts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    template_id INTEGER,
    name TEXT,
    field_values TEXT,       -- JSON: {field: value}
    status TEXT DEFAULT 'draft',  -- draft, completed, exported
    created_at TEXT,
    updated_at TEXT
);
```

---

## AI Roles

### 1. Template Analysis (when creating template)
**Prompt to AI:** "Analyze this legal document. Identify all variable fields that would change between different instances of this document type. For each field, determine: name (snake_case), display label (Serbian), data type, whether it's required, and the example value from this document."

### 2. Smart Fill (when filling document)
**Prompt to AI:** "Given this case description: '{free text}', extract the following field values for the template: {field list}. Return as JSON."

### 3. Validation (before export)
**Prompt to AI:** "Review this completed legal document for: missing information, formatting errors, internal consistency (dates, names match throughout), legal terminology correctness."

### 4. Export
- **PDF:** Use `reportlab` or `weasyprint` (HTML→PDF)
- **DOCX:** Use `python-docx` — fill template with values

---

## API Endpoints

```
# Templates
GET    /api/templates                     — list all templates
POST   /api/templates                     — create template (AI analyzes)
GET    /api/templates/{id}                — get template details
PUT    /api/templates/{id}                — update template
DELETE /api/templates/{id}                — delete template

# AI-assisted operations
POST   /api/templates/analyze             — analyze example document, extract fields
POST   /api/templates/{id}/smart-fill     — AI fills fields from free text description
POST   /api/templates/{id}/validate       — AI validates completed document

# Drafts
GET    /api/drafts                        — list user's drafts
POST   /api/drafts                        — create draft
GET    /api/drafts/{id}                   — get draft
PUT    /api/drafts/{id}                   — update draft field values
DELETE /api/drafts/{id}                   — delete draft

# Export
POST   /api/drafts/{id}/export/pdf        — export as PDF
POST   /api/drafts/{id}/export/docx       — export as DOCX
POST   /api/drafts/{id}/export/preview    — preview rendered document (HTML)
```

---

## Frontend: "Šabloni" section (under Moji dokumenti)

### Navigation update
Sidebar nav becomes:
- Razgovor
- Moji dokumenti
- **Šabloni dokumenata** ← NEW (icon: fa-file-signature)
- Podešavanja
- Pomoć i podrška
- Spoljni AI

### Page layout

**Tab 1: Šabloni (Template list)**
```
┌─────────────────────────────────────────────────┐
│ Šabloni dokumenata                               │
│ Kreirajte šablone i popunjavajte dokumente brže │
│                                                   │
│ [+ Novi šablon]                                   │
│                                                   │
│ ┌─ TUŽBE ──────────────────────────────────────┐│
│ │ ▪ Tužba za naknadu štete (8 polja)           ││
│ │   Poslednja izmena: 22.3.2026                ││
│ │                    [Popuni] [Izmeni] [Obriši] ││
│ └──────────────────────────────────────────────┘│
│ ┌─ UGOVORI ────────────────────────────────────┐│
│ │ ▪ Ugovor o zakupu (12 polja)                 ││
│ │                    [Popuni] [Izmeni] [Obriši] ││
│ └──────────────────────────────────────────────┘│
└─────────────────────────────────────────────────┘
```

**Tab 2: Fill document (when "Popuni" clicked)**
```
┌─────────────────────────────────────────────────┐
│ Popunjavanje: Tužba za naknadu štete            │
│                                                   │
│ [AI popuni iz opisa]  ← paste free text,        │
│                         AI extracts all fields   │
│                                                   │
│ Ime tužioca: [__________________________]       │
│ JMBG tužioca: [__________________________]  ✅  │
│ Adresa tužioca: [__________________________]    │
│ Ime tuženog: [__________________________]       │
│ Iznos: [__________________________] RSD         │
│ Datum: [__________________________]  ⚠️ Format │
│ Sud: [__________________________]               │
│ Opis spora:                                      │
│ ┌──────────────────────────────────────────────┐│
│ │                                              ││
│ └──────────────────────────────────────────────┘│
│                                                   │
│ ── Pregled dokumenta ─────────────────────────  │
│ [live preview of filled document]                │
│                                                   │
│ ── AI provera ────────────────────────────────  │
│ ✅ 7/8 polja popunjeno                          │
│ ⚠️ JMBG tužioca: nedostaje 1 cifra             │
│ ✅ Datum u ispravnom formatu                     │
│                                                   │
│ [Sačuvaj draft] [Izvezi PDF] [Izvezi DOCX]      │
└─────────────────────────────────────────────────┘
```
