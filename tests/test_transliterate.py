from core.transliterate import to_latin, to_cyrillic, detect_script


def test_cyrillic_to_latin():
    assert to_latin("Здраво свете") == "Zdravo svete"


def test_latin_to_cyrillic():
    assert to_cyrillic("Zdravo svete") == "Здраво свете"


def test_digraphs():
    assert to_latin("Љубав") == "Ljubav"
    assert to_latin("Његош") == "Njegoš"
    assert to_latin("Џеп") == "Džep"


def test_detect_script():
    assert detect_script("Здраво") == "cyrillic"
    assert detect_script("Zdravo") == "latin"


def test_legal_text():
    cyr = "Члан 5. Закона о раду"
    lat = "Član 5. Zakona o radu"
    assert to_latin(cyr) == lat
    assert to_cyrillic(lat) == cyr


def test_roundtrip():
    original = "Zakon o obligacionim odnosima"
    assert to_latin(to_cyrillic(original)) == original
