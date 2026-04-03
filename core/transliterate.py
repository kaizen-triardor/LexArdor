"""Serbian Cyrillic <-> Latin transliteration (1:1 bijective mapping)."""

_CYR_TO_LAT = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D',
    'Ђ': 'Đ', 'Е': 'E', 'Ж': 'Ž', 'З': 'Z', 'И': 'I',
    'Ј': 'J', 'К': 'K', 'Л': 'L', 'Љ': 'Lj', 'М': 'M',
    'Н': 'N', 'Њ': 'Nj', 'О': 'O', 'П': 'P', 'Р': 'R',
    'С': 'S', 'Т': 'T', 'Ћ': 'Ć', 'У': 'U', 'Ф': 'F',
    'Х': 'H', 'Ц': 'C', 'Ч': 'Č', 'Џ': 'Dž', 'Ш': 'Š',
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
    'ђ': 'đ', 'е': 'e', 'ж': 'ž', 'з': 'z', 'и': 'i',
    'ј': 'j', 'к': 'k', 'л': 'l', 'љ': 'lj', 'м': 'm',
    'н': 'n', 'њ': 'nj', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'ћ': 'ć', 'у': 'u', 'ф': 'f',
    'х': 'h', 'ц': 'c', 'ч': 'č', 'џ': 'dž', 'ш': 'š',
}

# Reverse: Latin -> Cyrillic (digraphs sorted longest-first for matching)
_LAT_TO_CYR = {}
for _c, _l in _CYR_TO_LAT.items():
    _LAT_TO_CYR[_l] = _c

_LAT_DIGRAPHS = sorted(
    [(lat, cyr) for cyr, lat in _CYR_TO_LAT.items() if len(lat) > 1],
    key=lambda x: -len(x[0]),
)

_CYR_CHARS = set(_CYR_TO_LAT.keys())
_LAT_CHARS = set(_LAT_TO_CYR.keys())


def to_latin(text: str) -> str:
    """Convert Serbian Cyrillic text to Latin."""
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def to_cyrillic(text: str) -> str:
    """Convert Serbian Latin text to Cyrillic."""
    result = []
    i = 0
    while i < len(text):
        matched = False
        for lat, cyr in _LAT_DIGRAPHS:
            if text[i:i + len(lat)] == lat:
                result.append(cyr)
                i += len(lat)
                matched = True
                break
        if not matched:
            result.append(_LAT_TO_CYR.get(text[i], text[i]))
            i += 1
    return "".join(result)


def detect_script(text: str) -> str:
    """Return 'cyrillic', 'latin', or 'mixed'."""
    has_cyr = any(c in _CYR_CHARS for c in text)
    has_lat = any(c in _LAT_CHARS for c in text)
    if has_cyr and has_lat:
        return "mixed"
    return "cyrillic" if has_cyr else "latin"
