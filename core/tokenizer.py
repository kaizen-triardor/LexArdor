"""Serbian legal text tokenizer — shared stop words and tokenization utilities."""
import re

# ── Serbian stop words (comprehensive, used by BM25 + confidence scoring) ────

SERBIAN_STOP_WORDS = frozenset({
    # Common function words
    "je", "u", "i", "na", "za", "da", "se", "sa", "od", "do", "iz",
    "po", "o", "koji", "koja", "koje", "li", "ili", "ni", "ne",
    "su", "bi", "ce", "će", "te", "tu", "to", "ta", "ova", "ovo", "sve",
    "prema", "kako", "sta", "šta", "kao", "kad", "ako", "vec", "već",
    "biti", "bio", "bila", "bilo", "sam", "si", "smo", "ste",
    "ovaj", "taj", "onaj", "ovi", "ti", "oni", "ona", "ono",
    # Legal connecting words (keep legal terms OUT of stop words)
    "ili", "odnosno", "takodje", "takođe", "medjutim", "međutim",
    "naime", "dakle", "stoga", "zato", "zbog",
})

# ── Legal domain terms (never remove these from queries) ────────────────────

LEGAL_TERMS = frozenset({
    "zakon", "član", "stav", "tačka", "alineja", "paragraf",
    "uredba", "pravilnik", "odluka", "naredba", "uputstvo",
    "ugovor", "tužba", "žalba", "rešenje", "presuda", "podnesak",
    "punomoćje", "dopis", "obaveštenje", "saglasnost",
    "pravno", "lice", "fizičko", "privredno", "društvo",
    "rok", "dan", "mesec", "godina", "kazna", "novčana",
    "sud", "tužilac", "tuženi", "zastupnik", "advokat",
    "pravo", "obaveza", "odgovornost", "naknada", "šteta",
    "radni", "odnos", "zaposleni", "poslodavac", "otkaz", "plata",
    "porez", "pdv", "dobit", "doprinosi", "budžet",
    "brak", "razvod", "alimentacija", "staratelj", "nasledstvo",
    "krivično", "delo", "zatvor", "pritvor", "tužilaštvo",
})


def tokenize(text: str, remove_stops: bool = True) -> list[str]:
    """Tokenize Serbian text for BM25/search purposes.

    - Lowercases
    - Splits on non-word characters
    - Removes stop words (unless they are legal terms)
    - Keeps tokens of 2+ characters
    """
    tokens = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if remove_stops:
        tokens = [
            t for t in tokens
            if len(t) >= 2 and (t not in SERBIAN_STOP_WORDS or t in LEGAL_TERMS)
        ]
    else:
        tokens = [t for t in tokens if len(t) >= 2]
    return tokens


def extract_query_keywords(query: str) -> set[str]:
    """Extract meaningful keywords from a user query (for confidence scoring)."""
    tokens = tokenize(query, remove_stops=True)
    return set(tokens)
