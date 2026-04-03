#!/usr/bin/env python3
"""Extract text from all remaining documents (OpenClaw + problematic court decisions).

Prepares JSON files ready for ChromaDB ingestion.

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m scripts.extract_remaining_docs --openclaw     # Extract OpenClaw docs
    python -m scripts.extract_remaining_docs --problematic  # OCR problematic court decisions
    python -m scripts.extract_remaining_docs --all          # Everything
    python -m scripts.extract_remaining_docs --status       # Show what we have
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Paths
LEXARDOR_DIR = Path(__file__).parent.parent
DATA_DIR = LEXARDOR_DIR / "data"
OPENCLAW_DIR = Path("/home/kaizenlinux/Projects/Project_02_LEXARDOR/aks-legal-documents")
PROB_DIR = DATA_DIR / "court_decisions" / "problematic"
PDF_DIR = DATA_DIR / "court_decisions" / "pdfs"

# Output directories for extracted texts
OPENCLAW_OUT = DATA_DIR / "openclaw_texts"
PROB_OUT = DATA_DIR / "court_decisions" / "texts"  # Same as main texts dir

# OpenClaw source directories
OPENCLAW_SOURCES = [
    ("laws-1862-today", OPENCLAW_DIR / "laws-1862-today" / "documents", "istorijski_zakon"),
    ("legal-regulations", OPENCLAW_DIR / "legal-regulations" / "documents", "propis"),
    ("branič-journal", OPENCLAW_DIR / "branič-journal" / "documents", "strucni_tekst"),
]


# ── Text extraction functions ───────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    """Extract text from PDF using PyMuPDF. Returns (text, method)."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        text = "\n".join(text_parts).strip()
        if len(text) > 50:
            return text, "pymupdf"
    except Exception:
        pass
    return "", "failed"


def extract_pdf_ocr(pdf_path: Path) -> tuple[str, str]:
    """Extract text from scanned PDF using Tesseract OCR. Returns (text, method)."""
    try:
        import pytesseract
        from pdf2image import convert_from_path

        # Check tesseract is available
        pytesseract.get_tesseract_version()

        images = convert_from_path(str(pdf_path), dpi=300)
        text_parts = []
        for img in images:
            # Try Serbian Cyrillic + Latin
            text = pytesseract.image_to_string(img, lang="srp+srp_latn", config="--psm 6")
            text_parts.append(text)
        text = "\n".join(text_parts).strip()
        if len(text) > 50:
            return text, "ocr_tesseract"
    except Exception as e:
        return "", f"ocr_failed: {e}"
    return "", "ocr_empty"


def extract_docx_text(docx_path: Path) -> tuple[str, str]:
    """Extract text from DOCX using python-docx. Returns (text, method)."""
    try:
        from docx import Document
        doc = Document(str(docx_path))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if len(text) > 50:
            return text, "python-docx"
    except Exception:
        pass
    return "", "failed"


def extract_doc_text(doc_path: Path) -> tuple[str, str]:
    """Extract text from old DOC format. Try antiword or LibreOffice."""
    # Try antiword first
    import subprocess
    try:
        result = subprocess.run(
            ["antiword", str(doc_path)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and len(result.stdout.strip()) > 50:
            return result.stdout.strip(), "antiword"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try LibreOffice convert to text
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "txt:Text",
                 "--outdir", tmpdir, str(doc_path)],
                capture_output=True, timeout=60
            )
            if result.returncode == 0:
                txt_files = list(Path(tmpdir).glob("*.txt"))
                if txt_files:
                    text = txt_files[0].read_text(encoding="utf-8", errors="replace").strip()
                    if len(text) > 50:
                        return text, "libreoffice"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "", "failed"


def extract_odt_text(odt_path: Path) -> tuple[str, str]:
    """Extract text from ODT. Try odfpy, fall back to LibreOffice."""
    try:
        from odf.opendocument import load
        from odf.text import P
        from odf import teletype
        doc = load(str(odt_path))
        paras = doc.getElementsByType(P)
        text = "\n".join(teletype.extractText(p) for p in paras).strip()
        if len(text) > 50:
            return text, "odfpy"
    except Exception:
        pass

    # Fall back to LibreOffice
    import subprocess, tempfile
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "txt:Text",
                 "--outdir", tmpdir, str(odt_path)],
                capture_output=True, timeout=60
            )
            if result.returncode == 0:
                txt_files = list(Path(tmpdir).glob("*.txt"))
                if txt_files:
                    text = txt_files[0].read_text(encoding="utf-8", errors="replace").strip()
                    if len(text) > 50:
                        return text, "libreoffice"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "", "failed"


def extract_image_ocr(img_path: Path) -> tuple[str, str]:
    """OCR an image file (JPG/PNG). Returns (text, method)."""
    try:
        import pytesseract
        from PIL import Image
        pytesseract.get_tesseract_version()
        img = Image.open(str(img_path))
        text = pytesseract.image_to_string(img, lang="srp+srp_latn", config="--psm 6")
        text = text.strip()
        if len(text) > 50:
            return text, "ocr_image"
    except Exception as e:
        return "", f"ocr_failed: {e}"
    return "", "ocr_empty"


def extract_any(file_path: Path) -> tuple[str, str]:
    """Extract text from any supported file type."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        text, method = extract_pdf_text(file_path)
        if text:
            return text, method
        # Fall back to OCR for scanned PDFs
        return extract_pdf_ocr(file_path)

    elif suffix == ".docx":
        return extract_docx_text(file_path)

    elif suffix == ".doc":
        return extract_doc_text(file_path)

    elif suffix == ".odt" or suffix == ".ott":
        return extract_odt_text(file_path)

    elif suffix in (".jpg", ".jpeg", ".png", ".gif"):
        return extract_image_ocr(file_path)

    elif suffix == ".xlsx":
        return "", "skipped_spreadsheet"

    return "", f"unsupported_{suffix}"


# ── OpenClaw extraction ─────────────────────────────────────────────────────

def process_openclaw():
    """Extract text from all OpenClaw documents."""
    OPENCLAW_OUT.mkdir(parents=True, exist_ok=True)

    total = 0
    good = 0
    failed = 0
    skipped = 0
    methods = {}
    start = time.time()

    for source_name, source_dir, doc_type in OPENCLAW_SOURCES:
        if not source_dir.exists():
            print(f"  Skipping {source_name}: directory not found")
            continue

        files = sorted(source_dir.iterdir())
        print(f"\n  Processing {source_name}: {len(files)} files")

        for f in files:
            if not f.is_file():
                continue

            total += 1
            out_path = OPENCLAW_OUT / f"{source_name}_{f.stem}.json"

            # Skip already processed
            if out_path.exists():
                skipped += 1
                continue

            text, method = extract_any(f)
            methods[method] = methods.get(method, 0) + 1

            if text:
                data = {
                    "id": f"{source_name}_{f.stem}",
                    "source": "openclaw",
                    "source_category": source_name,
                    "doc_type": doc_type,
                    "filename": f.name,
                    "full_text": text,
                    "text_length": len(text),
                    "extraction_method": method,
                    "source_url": f"https://aks.org.rs/",
                }
                out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                good += 1
            else:
                failed += 1

            if total % 50 == 0:
                elapsed = time.time() - start
                print(f"    [{total}] good={good} failed={failed} skipped={skipped} ({elapsed:.0f}s)")

    elapsed = time.time() - start
    print(f"\n  OpenClaw extraction complete ({elapsed:.0f}s)")
    print(f"    Total files: {total}")
    print(f"    Good:        {good}")
    print(f"    Failed:      {failed}")
    print(f"    Skipped:     {skipped}")
    print(f"    Methods:     {methods}")

    return good


# ── Problematic court decisions ─────────────────────────────────────────────

def process_problematic():
    """Re-extract problematic court decisions using OCR."""
    if not PROB_DIR.exists():
        print("  No problematic directory found")
        return 0

    prob_files = sorted(PROB_DIR.glob("*.json"))
    print(f"\n  Processing {len(prob_files)} problematic court decisions")

    good = 0
    failed = 0
    methods = {}

    for pf in prob_files:
        data = json.loads(pf.read_text())
        did = data.get("id", int(pf.stem))
        file_type = data.get("file_type", "pdf")

        # Find the original file
        pdf_path = PDF_DIR / f"{did}.pdf"
        if not pdf_path.exists():
            print(f"    {did}: original file not found")
            failed += 1
            continue

        # Try extraction based on actual file type
        if file_type in ("odt", "ott"):
            text, method = extract_odt_text(pdf_path)
            if not text:
                # Try as PDF (might be mislabeled)
                text, method = extract_pdf_text(pdf_path)
            if not text:
                text, method = extract_pdf_ocr(pdf_path)
        else:
            # PDF: try OCR directly (we know PyMuPDF already failed)
            text, method = extract_pdf_ocr(pdf_path)

        methods[method] = methods.get(method, 0) + 1

        if text:
            # Save to main texts directory
            out_data = {
                "id": did,
                "source_url": f"https://sudskapraksa.sud.rs/sudska-praksa/download/id/{did}/file/odluka",
                "full_text": text,
                "doc_type": "sudska_praksa",
                "file_type": file_type,
                "file_size": pdf_path.stat().st_size,
                "text_length": len(text),
                "extraction_method": method,
            }
            out_path = PROB_OUT / f"{did}.json"
            out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
            good += 1
            print(f"    {did}: OK ({method}, {len(text)} chars)")
        else:
            failed += 1
            print(f"    {did}: FAILED ({method})")

    print(f"\n  Problematic extraction complete")
    print(f"    Good:    {good}")
    print(f"    Failed:  {failed}")
    print(f"    Methods: {methods}")

    return good


# ── Status ──────────────────────────────────────────────────────────────────

def show_status():
    """Show status of all remaining documents."""
    print("\n=== REMAINING DOCUMENTS STATUS ===\n")

    # Problematic court decisions
    prob_count = len(list(PROB_DIR.glob("*.json"))) if PROB_DIR.exists() else 0
    # Check how many have been recovered
    recovered = 0
    for pf in PROB_DIR.glob("*.json") if PROB_DIR.exists() else []:
        did = pf.stem
        if (PROB_OUT / f"{did}.json").exists():
            recovered += 1
    print(f"Problematic court decisions: {prob_count} total, {recovered} recovered")

    # OpenClaw
    oc_count = len(list(OPENCLAW_OUT.glob("*.json"))) if OPENCLAW_OUT.exists() else 0
    for source_name, source_dir, doc_type in OPENCLAW_SOURCES:
        if source_dir.exists():
            files = len(list(source_dir.iterdir()))
            print(f"OpenClaw {source_name}: {files} source files")
    print(f"OpenClaw extracted: {oc_count} texts ready")

    # Total ready for ingestion
    print(f"\nTotal ready for next ingestion: {oc_count + recovered} documents")


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--status" in args:
        show_status()
    elif "--openclaw" in args or "--all" in args:
        process_openclaw()
        if "--all" in args:
            process_problematic()
        show_status()
    elif "--problematic" in args:
        process_problematic()
        show_status()
    else:
        print("Usage:")
        print("  python -m scripts.extract_remaining_docs --openclaw")
        print("  python -m scripts.extract_remaining_docs --problematic")
        print("  python -m scripts.extract_remaining_docs --all")
        print("  python -m scripts.extract_remaining_docs --status")
        show_status()
