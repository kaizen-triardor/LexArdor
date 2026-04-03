"""Extract text from PDF and DOCX files."""
from pathlib import Path
from io import BytesIO


def extract_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF with cleanup."""
    import pymupdf
    import re
    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    text = "\n\n".join(pages).strip()
    # Clean up common PDF extraction artifacts
    text = re.sub(r'\n{3,}', '\n\n', text)           # collapse excessive newlines
    text = re.sub(r'[ \t]{2,}', ' ', text)            # collapse multiple spaces
    text = re.sub(r'(\n\d+\s*\n)', '\n', text)        # remove standalone page numbers
    text = re.sub(r'-\n(\w)', r'\1', text)             # rejoin hyphenated words
    return text.strip()


def extract_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document
    doc = Document(BytesIO(file_bytes))
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs).strip()


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Auto-detect format and extract text."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return extract_from_pdf(file_bytes)
    elif ext in (".docx", ".doc"):
        return extract_from_docx(file_bytes)
    elif ext == ".txt":
        return file_bytes.decode("utf-8", errors="replace").strip()
    else:
        raise ValueError(f"Nepodržan format: {ext}. Koristite PDF, DOCX ili TXT.")
