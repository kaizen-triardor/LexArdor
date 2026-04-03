"""Tests for the Serbian law parser."""

import pytest
from scraper.parser import parse_law_text, extract_gazette, extract_title, slugify


SAMPLE_LAW_TEXT = """ZAKON O RADU

("Sl. glasnik RS", br. 24/2005, 61/2005, 54/2009, 32/2013, 75/2014, 13/2017 - odluka US, 113/2017 i 95/2018 - autentično tumačenje)

I. OSNOVNE ODREDBE

1. Predmet

Član 1.

Prava, obaveze i odgovornosti iz radnog odnosa, odnosno po osnovu rada, uređuju se ovim zakonom i posebnim zakonom, u skladu sa ratifikovanim međunarodnim konvencijama.

Član 2.

Odredbe ovog zakona primenjuju se na zaposlene koji rade na teritoriji Republike Srbije, kod domaćeg ili stranog pravnog, odnosno fizičkog lica.

II. ZASNIVANJE RADNOG ODNOSA

1. Uslovi za zasnivanje radnog odnosa

Član 24.

Radni odnos može da se zasnuje sa licem koje ima najmanje 15 godina života i ispunjava druge uslove za rad na određenim poslovima.

Član 24a.

Radni odnos sa licem mlađim od 18 godina života može da se zasnuje uz pismenu saglasnost roditelja.

III. RADNO VREME

Član 50.

Puno radno vreme iznosi 40 časova nedeljno, ako ovim zakonom nije drukčije određeno.
"""


class TestSlugify:
    def test_basic(self):
        assert slugify("ZAKON O RADU") == "zakon-o-radu"

    def test_serbian_chars(self):
        assert slugify("Zakon o zaštiti") == "zakon-o-zastiti"

    def test_strips_special(self):
        assert slugify("Zakon (prečišćen tekst)") == "zakon-preciscen-tekst"


class TestExtractGazette:
    def test_finds_gazette(self):
        result = extract_gazette(SAMPLE_LAW_TEXT)
        assert result is not None
        assert "Sl. glasnik RS" in result
        assert "24/2005" in result

    def test_no_gazette(self):
        assert extract_gazette("Nema službenog glasnika ovde") is None


class TestExtractTitle:
    def test_finds_title(self):
        title = extract_title(SAMPLE_LAW_TEXT)
        assert title == "ZAKON O RADU"

    def test_empty_text(self):
        title = extract_title("")
        assert title == "Nepoznat zakon"

    def test_multiline_title(self):
        text = "ZAKON O ZAŠTITI\nPODATAKA O LIČNOSTI\n\n(\"Sl. glasnik RS\"...)"
        title = extract_title(text)
        assert "ZAKON O ZAŠTITI" in title
        assert "PODATAKA O LIČNOSTI" in title


class TestParseLawText:
    def test_basic_parse(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        assert result["title"] == "ZAKON O RADU"
        assert result["gazette"] is not None
        assert "24/2005" in result["gazette"]
        assert result["article_count"] == 5

    def test_article_numbers(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        numbers = [a["number"] for a in result["articles"]]
        assert "1" in numbers
        assert "2" in numbers
        assert "24" in numbers
        assert "24a" in numbers
        assert "50" in numbers

    def test_article_text(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        art1 = next(a for a in result["articles"] if a["number"] == "1")
        assert "radnog odnosa" in art1["text"]

    def test_chapter_tracking(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        art1 = next(a for a in result["articles"] if a["number"] == "1")
        assert art1["chapter"] is not None
        assert "OSNOVNE ODREDBE" in art1["chapter"]

        art24 = next(a for a in result["articles"] if a["number"] == "24")
        assert art24["chapter"] is not None
        assert "ZASNIVANJE RADNOG ODNOSA" in art24["chapter"]

        art50 = next(a for a in result["articles"] if a["number"] == "50")
        assert art50["chapter"] is not None
        assert "RADNO VREME" in art50["chapter"]

    def test_slug_generated(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        assert result["slug"] == "zakon-o-radu"

    def test_article_24a_parsed(self):
        result = parse_law_text(SAMPLE_LAW_TEXT)
        art24a = next(a for a in result["articles"] if a["number"] == "24a")
        assert "mlađim od 18" in art24a["text"]

    def test_empty_text(self):
        result = parse_law_text("")
        assert result["article_count"] == 0
        assert result["articles"] == []
        assert result["title"] == "Nepoznat zakon"
        assert result["gazette"] is None
