# LexArdor UX/UI Redesign — Design Document

**Date:** 2026-03-22
**Status:** Approved via brainstorming session

---

## Brand Changes

- **Name:** LexArdor (no emoji, no icons next to name)
- **Tagline:** "AI pripravnik" (replaces "Stoic AI Co-Counsel")
- **Typography:** "LexArdor" in serif/display font, "AI pripravnik" in small muted text below
- **Colors:** Deep navy (#1B365D) primary, Copper/ember (#C9A84C) accent, Dark bg (#0a0e17)
- **Tone:** Professional legal tool, not a chatbot. No emoji in UI chrome.

## Target Users

Mix of junior lawyers and paralegal/office staff in Serbian law firms. UI must be:
- Fast for power users (keyboard shortcuts, quick answers)
- Helpful for beginners (FAQ, confidence indicators, clear sources)

## Page Layout

Sidebar (240px) + Main area. Sidebar contains:
1. Logo: "LexArdor" / "AI pripravnik" (clean text, no emoji)
2. [+ Nova konverzacija] button
3. Chat history list (scrollable)
4. Navigation: Razgovor, Moji dokumenti, Podešavanja, Pomoć i podrška
5. Status bar: AI health, corpus count, version + last update date

## Pages

### 1. Razgovor (Chat) — default page
- Chat messages with markdown rendering
- User messages right-aligned, AI messages left-aligned
- Each AI response shows:
  - The answer with markdown formatting
  - Confidence bar: ■■■■□ Visoka / ■■■□□ Srednja / ■■□□□ Niska
  - Expandable sources section showing law name, article number, gazette
  - Sources labeled as "Zakon" or "Vaš dokument" based on which collection
- Input area: textarea + "Pošalji" button + "Dublja analiza" checkbox
- "Dublja analiza" uses 27B model (client doesn't see model names)
- Legal disclaimer always visible at bottom (subtle, muted)

### 2. Moji dokumenti (My Documents)
- List of uploaded documents grouped by category
- Categories: Ugovor, Presuda, Tužba, Rešenje, Dopis, Ostalo
- Upload modal: title, category dropdown, text area (paste text)
- Each doc shows: title, date, page count, category, [Pregledaj] [Obriši]
- Info text: "Ovi dokumenti su dostupni samo vama."
- Documents stored in client_documents ChromaDB collection (separate from laws)

### 3. Podešavanja (Settings)
- Account info: username, role, [Promeni lozinku]
- Script toggle: Latinica / Ćirilica radio buttons
- AI model info: current model name, status indicator
- Corpus stats: law articles count, client docs count
- App info: version, license (firm name), installation ID, last update date

### 4. Pomoć i podrška (Help & Support)
- FAQ section: expandable accordion with 5-6 common questions
- Report form:
  - Type dropdown: Problem sa odgovorom, Aplikacija ne radi, Predlog za poboljšanje, Pitanje o korišćenju, Ostalo
  - Description textarea
  - Checkbox: "Priloži poslednju konverzaciju"
  - Submit → mailto: link to kaizen.triardor@gmail.com
  - Subject format: [LexArdor] #LA-2026-XXXX - {type}
- Report history: list of locally saved past reports
- Contact info: email, installation ID

## Email (mailto) Format

```
To: kaizen.triardor@gmail.com
Subject: [LexArdor] #LA-2026-0001 - Problem sa odgovorom
Body:
Instalacija: #LA-2026-0001
Licenca: Advokatska kancelarija Petrović
Verzija: 2.0
Datum: 22.3.2026 14:30

Vrsta: Problem sa odgovorom

Opis:
{user's description}

--- Poslednja konverzacija ---
Pitanje: {last user query}
Odgovor: {last AI response}
Pouzdanost: {confidence}
Izvori: {sources list}
```

## Update System

- v1: Manual push via Tailscale SSH
- App shows "Poslednje ažuriranje: DD.MM.YYYY" in Settings
- Future: auto-check notification (not for MVP)

## Deployment per Client

1. setup_client.py sets firm name, admin user, installation ID
2. Core laws (ChromaDB) read-only, owned by service account
3. Client documents read-write via UI only
4. Tailscale installed for remote management
5. LaunchDaemon auto-starts on boot (Mac)

## Installation ID Format

`#LA-{year}-{sequential 4-digit}`
Example: #LA-2026-0001, #LA-2026-0002

Stored in .env as INSTALLATION_ID, shown in Settings and included in all support emails.
