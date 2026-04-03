#!/usr/bin/env python3
"""Generate training dataset for fine-tuning Qwen 3.5 on Serbian legal domain.

Creates 1000+ Q&A pairs from real law articles in the LexArdor database.
Each pair has: question, source article text, ideal concise answer with citations.

Output: data/training/lexardor_legal_qa.jsonl
"""
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / "data" / "lexardor.db"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "training"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Question templates per legal domain ──────────────────────────────────────

QUESTION_TEMPLATES = {
    "radno_pravo": [
        "Šta propisuje {law} u članu {art} o {topic}?",
        "Koja su prava zaposlenog prema članu {art} {law}?",
        "Koje su obaveze poslodavca prema članu {art} {law}?",
        "Da li je dozvoljeno {topic} prema {law}?",
        "Koji su uslovi za {topic} prema srpskom Zakonu o radu?",
        "Kako {law} reguliše {topic}?",
        "Šta kaže član {art} {law} o {topic}?",
        "Koji su rokovi za {topic} prema {law}?",
        "Da li poslodavac može {topic}?",
        "Koja je kazna za {topic} prema {law}?",
    ],
    "obligaciono_pravo": [
        "Kako {law} definiše {topic}?",
        "Koja su prava ugovornih strana prema članu {art} {law}?",
        "Šta propisuje {law} o {topic}?",
        "Koji su uslovi za raskid ugovora prema {law}?",
        "Kako se reguliše {topic} u srpskom obligacionom pravu?",
        "Da li je moguće {topic} prema {law}?",
        "Koje su posledice {topic} prema članu {art} {law}?",
    ],
    "krivicno_pravo": [
        "Šta propisuje {law} za krivično delo {topic}?",
        "Koja je kazna za {topic} prema {law}?",
        "Kako {law} definiše {topic}?",
        "Koji su elementi krivičnog dela {topic} prema srpskom pravu?",
        "Da li je {topic} krivično delo prema {law}?",
        "Koje su olakšavajuće okolnosti za {topic} prema {law}?",
    ],
    "porodicno_pravo": [
        "Šta propisuje {law} o {topic}?",
        "Koja su prava deteta prema {law}?",
        "Kako se reguliše {topic} u srpskom porodičnom pravu?",
        "Koji su uslovi za {topic} prema {law}?",
        "Da li je potrebna saglasnost za {topic} prema {law}?",
    ],
    "privredno_pravo": [
        "Kako {law} reguliše {topic}?",
        "Koji su uslovi za {topic} prema {law}?",
        "Šta propisuje član {art} {law} o {topic}?",
        "Koje su obaveze privrednog društva prema {law}?",
        "Da li je potrebno {topic} prema srpskom privrednom pravu?",
    ],
    "poresko_pravo": [
        "Kako se obračunava {topic} prema {law}?",
        "Koji su rokovi za {topic} prema {law}?",
        "Šta propisuje {law} o {topic}?",
        "Da li je {topic} oporezivo prema srpskom pravu?",
    ],
    "upravno_pravo": [
        "Koji je postupak za {topic} prema {law}?",
        "Šta propisuje {law} o {topic}?",
        "Koji su rokovi za žalbu na {topic} prema {law}?",
        "Da li je potrebna dozvola za {topic} prema {law}?",
    ],
    "opste": [
        "Šta propisuje {law} u članu {art}?",
        "Kako član {art} {law} reguliše {topic}?",
        "Šta kaže srpsko pravo o {topic}?",
        "Koji su pravni uslovi za {topic}?",
        "Objasni član {art} {law}.",
    ],
}

# ── Law slug → domain mapping ────────────────────────────────────────────────

LAW_DOMAINS = {}  # Will be auto-detected

# Auto-detect domain from slug keywords
DOMAIN_KEYWORDS = {
    "radno_pravo": ["rad", "zaposlen", "kolektivni_ugovor", "kolektivni-ugovor", "strajk", "sindikat", "plat"],
    "obligaciono_pravo": ["obligaci", "ugovor", "potrosac", "zastit", "zakup", "najam", "osiguran"],
    "krivicno_pravo": ["krivicn", "krivičn", "kazneni", "prekrsaj"],
    "porodicno_pravo": ["porodic", "porodičn", "brak", "dete", "deca", "usvojen", "staratelj"],
    "privredno_pravo": ["privredno", "privreda", "kompanij", "drustvo", "društvo", "stecaj", "likvidac", "registrac"],
    "poresko_pravo": ["porez", "pdv", "dohodak", "dobit", "fiskalni", "carin", "akciz", "budzet"],
    "upravno_pravo": ["upravn", "inspekcij", "parnicn", "parnični", "izvrsenju", "izvršenj", "sud", "tuzilas"],
}

def detect_domain(slug: str) -> str:
    """Auto-detect legal domain from law slug."""
    slug_lower = slug.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in slug_lower:
                return domain
    return "opste"


def extract_topic_from_text(text: str) -> str:
    """Extract a short topic description from article text."""
    # Take first sentence, clean it up
    text = text.strip()
    # Remove "Član X." prefix
    text = re.sub(r'^Član\s+\d+[a-z]?\.\s*', '', text)
    # Remove stav markers
    text = re.sub(r'^\[s\d+\]\s*', '', text)
    # Take first meaningful sentence
    sentences = re.split(r'[.!?]\s+', text)
    if sentences:
        topic = sentences[0].strip()
        # Trim to reasonable length
        if len(topic) > 80:
            topic = topic[:77] + "..."
        return topic.lower()
    return "pravna regulativa"


def generate_answer(law_title: str, article_number: str, full_text: str, domain: str) -> str:
    """Generate an ideal concise answer based on article text."""
    # Clean article text
    text = full_text.strip()
    text = re.sub(r'^\[s\d+\]\s*', '', text)

    # Extract key content (first 500 chars)
    content = text[:500].strip()
    if len(text) > 500:
        content += "..."

    # Build concise answer
    answer = f"Prema Članu {article_number} {law_title}, {content}"

    # Add disclaimer
    answer += "\n\nNapomena: Ovo je informativni odgovor. Za konkretne pravne savete konsultujte advokata."

    return answer


def generate_reasoning_chain(law_title: str, article_number: str, full_text: str, question: str) -> str:
    """Generate a reasoning chain showing how to think about the legal question."""
    text = full_text[:400].strip()

    chain = f"""RAZMIŠLJANJE:
1. Korisnik pita o: {question[:80]}
2. Relevantni propis: {law_title}, Član {article_number}
3. Tekst člana kaže: "{text[:200]}..."
4. Zaključak: Na osnovu ovog člana, mogu dati jasan odgovor.

ODGOVOR:
Prema Članu {article_number} {law_title}, {text[:300]}

Napomena: Ovo je informativni odgovor zasnovan na tekstu zakona."""

    return chain


def main():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Get all articles with substantial content from important laws
    articles = db.execute("""
        SELECT la.article_number, la.full_text, ld.slug, ld.title, ld.doc_type
        FROM legal_articles la
        JOIN legal_documents ld ON la.document_id = ld.id
        WHERE la.full_text IS NOT NULL
        AND LENGTH(la.full_text) > 100
        AND LENGTH(la.full_text) < 3000
        AND ld.doc_type IN ('zakon', 'zakonik', 'ustav')
        ORDER BY RANDOM()
    """).fetchall()

    print(f"Total eligible articles: {len(articles)}")

    dataset = []
    used_articles = set()

    # Generate Q&A pairs
    for art in articles:
        if len(dataset) >= 1000:
            break

        slug = art["slug"]
        art_num = art["article_number"]
        full_text = art["full_text"]
        title = art["title"]
        doc_type = art["doc_type"]

        # Skip if already used this article
        key = f"{slug}_{art_num}"
        if key in used_articles:
            continue
        used_articles.add(key)

        # Determine domain
        domain = detect_domain(slug)

        # Get topic from text
        topic = extract_topic_from_text(full_text)
        if len(topic) < 10:
            continue

        # Clean law title
        law_name = title
        if law_name.startswith('("'):
            law_name = slug.replace("_", " ").replace("-", " ").title()

        # Pick a question template
        templates = QUESTION_TEMPLATES.get(domain, QUESTION_TEMPLATES["opste"])
        template = random.choice(templates)

        try:
            question = template.format(law=law_name, art=art_num, topic=topic)
        except (KeyError, IndexError):
            question = f"Šta propisuje {law_name} u članu {art_num}?"

        # Generate answer (concise, with citation)
        answer = generate_answer(law_name, art_num, full_text, domain)

        # Generate reasoning chain (for chain-of-thought training)
        reasoning = generate_reasoning_chain(law_name, art_num, full_text, question)

        # Create training example in multiple formats

        # Format 1: Direct Q&A (for instruction tuning)
        dataset.append({
            "instruction": question,
            "input": f"PRAVNI IZVOR: {law_name}, Član {art_num}\n\n{full_text[:800]}",
            "output": answer,
            "domain": domain,
            "law_slug": slug,
            "article": art_num,
            "format": "direct",
        })

        # Format 2: Chain-of-thought (every 3rd example)
        if len(dataset) % 3 == 0:
            dataset.append({
                "instruction": question,
                "input": f"PRAVNI IZVOR: {law_name}, Član {art_num}\n\n{full_text[:800]}",
                "output": reasoning,
                "domain": domain,
                "law_slug": slug,
                "article": art_num,
                "format": "chain_of_thought",
            })

        # Format 3: "No source" examples (every 10th — teach model to say "I don't know")
        if len(dataset) % 10 == 0:
            fake_q = f"Da li {law_name} reguliše vanzemaljsku trgovinu?"
            dataset.append({
                "instruction": fake_q,
                "input": f"PRAVNI IZVOR: {law_name}, Član {art_num}\n\n{full_text[:400]}",
                "output": "Dostupni pravni izvori ne sadrže informacije o ovom pitanju. Preporučujem konsultaciju sa advokatom specijalizovanim za ovu oblast.",
                "domain": domain,
                "law_slug": slug,
                "article": art_num,
                "format": "no_answer",
            })

    db.close()

    # Shuffle
    random.shuffle(dataset)

    # Save JSONL
    output_path = OUTPUT_DIR / "lexardor_legal_qa.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        for item in dataset:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Also save as Hugging Face compatible format
    hf_dataset = []
    for item in dataset:
        # Alpaca format
        hf_dataset.append({
            "instruction": item["instruction"],
            "input": item["input"],
            "output": item["output"],
        })

    hf_path = OUTPUT_DIR / "lexardor_legal_qa_alpaca.json"
    with open(hf_path, "w", encoding="utf-8") as f:
        json.dump(hf_dataset, f, ensure_ascii=False, indent=2)

    # Stats
    domains = {}
    formats = {}
    for d in dataset:
        domains[d["domain"]] = domains.get(d["domain"], 0) + 1
        formats[d["format"]] = formats.get(d["format"], 0) + 1

    print(f"\nGenerated {len(dataset)} training examples")
    print(f"Saved to: {output_path}")
    print(f"HF format: {hf_path}")
    print(f"\nBy domain:")
    for k, v in sorted(domains.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"\nBy format:")
    for k, v in sorted(formats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
