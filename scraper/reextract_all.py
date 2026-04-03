"""Re-extract text from ALL court decision files, detecting real file type.

The download endpoint serves files as 'odluka' regardless of actual format.
Real types found: PDF (26K), ODT (35K), DOC (4.7K), ODT Template (3.7K),
                  DOCX (1), RTF (2), OO 1.x (1)

This script:
1. Detects real file type via magic bytes
2. Extracts text with the appropriate library
3. Moves previously-problematic files to texts/ if extraction succeeds

Usage:
    python scraper/reextract_all.py                    # Re-extract problematic files only
    python scraper/reextract_all.py --all              # Re-extract everything (skip existing good texts)
    python scraper/reextract_all.py --force             # Force re-extract ALL (overwrite existing)
    python scraper/reextract_all.py --status            # Show extraction stats
    python scraper/reextract_all.py --rename            # Rename files to correct extensions
"""
import argparse
import io
import json
import re
import struct
import sys
import time
import zipfile
from pathlib import Path

BASE_URL = "https://sudskapraksa.sud.rs"
DATA_DIR = Path(__file__).parent.parent / "data" / "court_decisions"
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "texts"
PROBLEM_DIR = DATA_DIR / "problematic"


# ── File type detection via magic bytes ──────────────────────────────────

def detect_file_type(file_bytes: bytes) -> str:
    """Detect real file type from magic bytes."""
    if len(file_bytes) < 8:
        return "unknown"

    # PDF: %PDF
    if file_bytes[:5] == b'%PDF-':
        return "pdf"

    # ZIP-based formats (DOCX, ODT, ODS, etc.)
    if file_bytes[:4] == b'PK\x03\x04':
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                names = zf.namelist()
                # ODT/OTT
                if 'content.xml' in names and 'mimetype' in names:
                    mime = zf.read('mimetype').decode('utf-8', errors='ignore').strip()
                    if 'opendocument.text-template' in mime:
                        return "ott"
                    if 'opendocument.text' in mime:
                        return "odt"
                    if 'opendocument' in mime:
                        return "odt"  # treat all ODF as ODT for text extraction
                # DOCX
                if '[Content_Types].xml' in names:
                    if any('word/' in n for n in names):
                        return "docx"
                # OpenOffice 1.x
                if 'content.xml' in names:
                    return "odt"
                return "zip_unknown"
        except (zipfile.BadZipFile, Exception):
            return "zip_corrupt"

    # OLE2 Compound Document (DOC, XLS, PPT, etc.)
    if file_bytes[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return "doc"

    # RTF
    if file_bytes[:5] == b'{\\rtf':
        return "rtf"

    # HTML
    lower = file_bytes[:500].lower()
    if b'<html' in lower or b'<!doctype html' in lower:
        return "html"

    # Plain text (heuristic: mostly printable + whitespace)
    try:
        sample = file_bytes[:2000].decode('utf-8')
        printable = sum(1 for c in sample if c.isprintable() or c in '\n\r\t')
        if printable / len(sample) > 0.85:
            return "txt"
    except UnicodeDecodeError:
        try:
            sample = file_bytes[:2000].decode('windows-1250')
            printable = sum(1 for c in sample if c.isprintable() or c in '\n\r\t')
            if printable / len(sample) > 0.85:
                return "txt_cp1250"
        except:
            pass

    return "unknown"


# ── Text extraction per format ───────────────────────────────────────────

def extract_pdf(file_bytes: bytes) -> tuple[str, int]:
    """Extract text from PDF. Returns (text, page_count)."""
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    page_count = len(doc)
    doc.close()
    return text.strip(), page_count


def extract_odt(file_bytes: bytes) -> str:
    """Extract text from ODT/OTT using odfpy."""
    from odf.opendocument import load
    from odf import text as odf_text
    from odf.element import Element

    def get_all_text(element):
        """Recursively extract text from ODF elements."""
        result = []
        if hasattr(element, 'childNodes'):
            for child in element.childNodes:
                if hasattr(child, 'data'):
                    result.append(child.data)
                elif hasattr(child, 'qname'):
                    qname = str(child.qname[1]) if isinstance(child.qname, tuple) else str(child.qname)
                    # Add line breaks for paragraphs
                    if qname in ('p', 'h'):
                        child_text = get_all_text(child)
                        if child_text:
                            result.append(child_text)
                            result.append('\n')
                    elif qname == 'tab':
                        result.append('\t')
                    elif qname == 'line-break':
                        result.append('\n')
                    elif qname == 's':
                        # Spaces
                        count = 1
                        try:
                            count = int(child.getAttribute('c') or 1)
                        except:
                            pass
                        result.append(' ' * count)
                    else:
                        result.append(get_all_text(child))
        return ''.join(result)

    doc = load(io.BytesIO(file_bytes))
    body = doc.body
    return get_all_text(body).strip()


def extract_doc(file_bytes: bytes) -> str:
    """Extract text from old-format DOC using olefile + custom parsing."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(file_bytes))

    # Try WordDocument stream
    if ole.exists('WordDocument'):
        try:
            # Try getting the plain text from the Word Binary Format
            # The text is stored in the WordDocument stream with complex encoding
            # Simpler approach: look for 1Table or 0Table + text pieces
            word_stream = ole.openstream('WordDocument').read()

            # FIB (File Information Block) structure
            # Offset 0x000A: flags (2 bytes) — bit 9 indicates 1Table vs 0Table
            if len(word_stream) < 0x200:
                raise ValueError("WordDocument too small")

            flags = struct.unpack_from('<H', word_stream, 0x000A)[0]
            table_name = '1Table' if (flags & 0x0200) else '0Table'

            if not ole.exists(table_name):
                # Fallback: try both
                table_name = '1Table' if ole.exists('1Table') else '0Table'

            if ole.exists(table_name):
                table_stream = ole.openstream(table_name).read()

                # Read CLX from table stream (FIB gives offset/size)
                # CLX offset at FIB 0x01A2 (4 bytes), size at 0x01A6 (4 bytes)
                clx_offset = struct.unpack_from('<I', word_stream, 0x01A2)[0]
                clx_size = struct.unpack_from('<I', word_stream, 0x01A6)[0]

                if clx_offset > 0 and clx_size > 0 and clx_offset + clx_size <= len(table_stream):
                    clx = table_stream[clx_offset:clx_offset + clx_size]

                    # Parse piece table from CLX
                    # Skip any Grpprl (type 0x01) entries
                    pos = 0
                    text_parts = []

                    while pos < len(clx):
                        entry_type = clx[pos]
                        if entry_type == 0x01:
                            # Grpprl — skip
                            if pos + 2 >= len(clx):
                                break
                            grpprl_size = struct.unpack_from('<H', clx, pos + 1)[0]
                            pos += 3 + grpprl_size
                        elif entry_type == 0x02:
                            # Piece table
                            if pos + 4 >= len(clx):
                                break
                            pt_size = struct.unpack_from('<I', clx, pos + 1)[0]
                            pt_data = clx[pos + 5:pos + 5 + pt_size]

                            # Number of pieces = (size - 4) / 12 (approximately)
                            # CPs are (n+1) * 4 bytes, then n * 8 bytes (PCD entries)
                            # Find n: n * 4 + 4 + n * 8 = pt_size → n = (pt_size - 4) / 12
                            n = (pt_size - 4) // 12
                            if n <= 0:
                                break

                            # Read character positions (n+1 CPs, each 4 bytes)
                            cps = []
                            for i in range(n + 1):
                                if i * 4 + 4 <= len(pt_data):
                                    cps.append(struct.unpack_from('<I', pt_data, i * 4)[0])

                            # Read piece descriptors (after CPs)
                            pcd_start = (n + 1) * 4
                            for i in range(n):
                                if pcd_start + i * 8 + 8 > len(pt_data):
                                    break
                                pcd = pt_data[pcd_start + i * 8:pcd_start + i * 8 + 8]
                                # Bytes 2-5: FC (file character position)
                                fc = struct.unpack_from('<I', pcd, 2)[0]

                                char_count = cps[i + 1] - cps[i] if i + 1 < len(cps) else 0
                                if char_count <= 0 or char_count > 1000000:
                                    continue

                                is_unicode = not (fc & 0x40000000)
                                real_fc = fc & 0x3FFFFFFF

                                if is_unicode:
                                    byte_count = char_count * 2
                                    if real_fc + byte_count <= len(word_stream):
                                        raw = word_stream[real_fc:real_fc + byte_count]
                                        text_parts.append(raw.decode('utf-16-le', errors='replace'))
                                else:
                                    real_fc = real_fc // 2
                                    if real_fc + char_count <= len(word_stream):
                                        raw = word_stream[real_fc:real_fc + char_count]
                                        # Try cp1250 first (Serbian), then cp1252
                                        try:
                                            text_parts.append(raw.decode('cp1250', errors='replace'))
                                        except:
                                            text_parts.append(raw.decode('cp1252', errors='replace'))
                            break
                        else:
                            break

                    if text_parts:
                        text = ''.join(text_parts)
                        # Clean up control characters but keep newlines
                        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
                        text = text.replace('\r\n', '\n').replace('\r', '\n')
                        ole.close()
                        return text.strip()

        except Exception:
            pass

    # Fallback: brute-force text extraction from all streams
    text_parts = []
    for stream_name in ole.listdir():
        try:
            data = ole.openstream(stream_name).read()
            # Try UTF-16
            if len(data) > 100:
                try:
                    decoded = data.decode('utf-16-le', errors='ignore')
                    printable = sum(1 for c in decoded[:500] if c.isprintable() or c in '\n\r\t ')
                    if printable > 200:
                        text_parts.append(decoded)
                        continue
                except:
                    pass
                # Try cp1250
                try:
                    decoded = data.decode('cp1250', errors='ignore')
                    printable = sum(1 for c in decoded[:500] if c.isprintable() or c in '\n\r\t ')
                    if printable > 200:
                        text_parts.append(decoded)
                except:
                    pass
        except:
            pass

    ole.close()
    result = '\n'.join(text_parts)
    result = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', result)
    return result.strip()


def extract_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX."""
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return '\n'.join(p.text for p in doc.paragraphs).strip()


def extract_rtf(file_bytes: bytes) -> str:
    """Extract text from RTF."""
    from striprtf.striprtf import rtf_to_text
    raw = file_bytes.decode('utf-8', errors='replace')
    return rtf_to_text(raw).strip()


def extract_html(file_bytes: bytes) -> str:
    """Extract text from HTML."""
    from bs4 import BeautifulSoup
    # Try UTF-8 first, then cp1250
    for enc in ['utf-8', 'cp1250', 'latin-1']:
        try:
            html = file_bytes.decode(enc)
            break
        except:
            html = file_bytes.decode('latin-1', errors='replace')
    soup = BeautifulSoup(html, 'html.parser')
    # Remove script/style
    for tag in soup(['script', 'style']):
        tag.decompose()
    return soup.get_text(separator='\n').strip()


def extract_txt(file_bytes: bytes, encoding: str = 'utf-8') -> str:
    """Extract text from plain text file."""
    for enc in [encoding, 'utf-8', 'cp1250', 'latin-1']:
        try:
            return file_bytes.decode(enc).strip()
        except:
            continue
    return file_bytes.decode('latin-1', errors='replace').strip()


def extract_text(file_path: Path) -> tuple[str, str, int]:
    """Universal text extraction. Returns (text, file_type, page_count)."""
    file_bytes = file_path.read_bytes()
    file_type = detect_file_type(file_bytes)
    page_count = 0

    if file_type == "pdf":
        text, page_count = extract_pdf(file_bytes)
        return text, file_type, page_count
    elif file_type in ("odt", "ott"):
        return extract_odt(file_bytes), file_type, 0
    elif file_type == "doc":
        return extract_doc(file_bytes), file_type, 0
    elif file_type == "docx":
        return extract_docx(file_bytes), file_type, 0
    elif file_type == "rtf":
        return extract_rtf(file_bytes), file_type, 0
    elif file_type == "html":
        return extract_html(file_bytes), file_type, 0
    elif file_type in ("txt", "txt_cp1250"):
        enc = "cp1250" if file_type == "txt_cp1250" else "utf-8"
        return extract_txt(file_bytes, enc), file_type, 0
    else:
        return "", file_type, 0


# ── Metadata extraction ─────────────────────────────────────────────────

def extract_metadata(text: str) -> dict:
    """Extract case metadata from text content."""
    meta = {}
    if not text:
        return meta

    for line in text.split("\n")[:40]:
        line = line.strip()
        if not line:
            continue
        # Case number (Cyrillic)
        if re.search(r'(?:Рев|Прев|Гж|Кж|Уж|Рж|Ку|Кзз|Узп|Пзз)\s*\.?\s*\d+/\d+', line) and "case_number" not in meta:
            meta["case_number"] = line
        # Case number (Latin)
        elif re.search(r'(?:Rev|Prev|Gž|Kž|Už|Rž|Ku|Kzz|Uzp|Pzz)\s*\.?\s*\d+/\d+', line, re.IGNORECASE) and "case_number" not in meta:
            meta["case_number"] = line
        # Date
        elif re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', line) and "date" not in meta:
            meta["date"] = line
        # Court (Cyrillic)
        elif any(k in line for k in ["суд", "Суд", "СУД"]) and "court" not in meta:
            meta["court"] = line
        # Court (Latin)
        elif any(k in line.lower() for k in ["sud ", "sud,", "viši sud", "osnovni sud", "apelacioni"]) and "court" not in meta:
            meta["court"] = line

    return meta


# ── Main extraction logic ───────────────────────────────────────────────

def reextract(mode: str = "problematic"):
    """Re-extract text from court decision files.

    Modes:
    - 'problematic': Only re-extract files in problematic/ folder
    - 'all': Extract all, skip existing good texts
    - 'force': Force re-extract everything
    """
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    PROBLEM_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which files to process
    all_files = sorted(PDF_DIR.glob("*.pdf"))
    to_process = []

    if mode == "force":
        to_process = all_files
    elif mode == "all":
        for f in all_files:
            text_path = TEXT_DIR / f"{f.stem}.json"
            if not text_path.exists():
                to_process.append(f)
    else:  # problematic only
        prob_files = set(f.stem for f in PROBLEM_DIR.glob("*.json"))
        to_process = [f for f in all_files if f.stem in prob_files]

    if not to_process:
        print("  Nothing to process!")
        return

    stats = {"pdf": 0, "odt": 0, "ott": 0, "doc": 0, "docx": 0,
             "rtf": 0, "html": 0, "txt": 0, "unknown": 0,
             "good": 0, "failed": 0, "skipped": 0}

    print(f"\n  Processing {len(to_process)} files (mode: {mode})...")
    start_time = time.time()

    for i, file_path in enumerate(to_process):
        doc_id = file_path.stem
        text_path = TEXT_DIR / f"{doc_id}.json"
        prob_path = PROBLEM_DIR / f"{doc_id}.json"

        # Skip if already good (unless force mode)
        if mode != "force" and text_path.exists():
            stats["skipped"] += 1
            continue

        try:
            text, file_type, page_count = extract_text(file_path)
            stats[file_type] = stats.get(file_type, 0) + 1

            data = {
                "id": int(doc_id),
                "source_url": f"{BASE_URL}/sudska-praksa/download/id/{doc_id}/file/odluka",
                "full_text": text,
                "doc_type": "sudska_praksa",
                "file_type": file_type,
                "file_size": file_path.stat().st_size,
                "text_length": len(text),
            }
            if page_count:
                data["page_count"] = page_count

            # Add metadata
            meta = extract_metadata(text)
            data.update(meta)

            if text and len(text) > 50:
                text_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                # Remove from problematic if it was there
                if prob_path.exists():
                    prob_path.unlink()
                stats["good"] += 1
            else:
                data["problem"] = "empty_after_extraction"
                data["reason"] = f"{file_type}: {len(text)} chars from {file_path.stat().st_size} bytes"
                prob_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                # Remove from texts if it was there
                if text_path.exists():
                    text_path.unlink()
                stats["failed"] += 1

        except Exception as e:
            stats["failed"] += 1
            error_data = {
                "id": int(doc_id),
                "problem": "extraction_error",
                "reason": str(e),
                "file_size": file_path.stat().st_size,
            }
            # Detect type even on error
            try:
                file_bytes = file_path.read_bytes()
                ft = detect_file_type(file_bytes)
                error_data["file_type"] = ft
                stats[ft] = stats.get(ft, 0) + 1
            except:
                pass
            prob_path.write_text(json.dumps(error_data, ensure_ascii=False, indent=2))

        # Progress
        done = stats["good"] + stats["failed"] + stats["skipped"]
        if done % 1000 == 0 or done == len(to_process):
            elapsed = time.time() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            remaining = (len(to_process) - done) / rate if rate > 0 else 0
            print(f"    [{done}/{len(to_process)}] good={stats['good']} failed={stats['failed']} "
                  f"({rate:.0f}/s, ~{remaining/60:.0f}m left)")

    elapsed = time.time() - start_time
    print(f"\n  ═══ Extraction Complete ({elapsed:.0f}s) ═══")
    print(f"  Good:     {stats['good']}")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"\n  By file type:")
    for ft in ["pdf", "odt", "ott", "doc", "docx", "rtf", "html", "txt", "unknown"]:
        if stats.get(ft, 0) > 0:
            print(f"    {ft:>8}: {stats[ft]}")


def rename_files():
    """Rename .pdf files to their correct extensions (optional, for inspection)."""
    RENAMED_DIR = DATA_DIR / "renamed"
    RENAMED_DIR.mkdir(parents=True, exist_ok=True)

    stats = {}
    for f in sorted(PDF_DIR.glob("*.pdf")):
        file_bytes = f.read_bytes()
        ft = detect_file_type(file_bytes)
        stats[ft] = stats.get(ft, 0) + 1

    print("\n  File type distribution:")
    for ft, count in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"    {ft:>12}: {count:>6}")
    print(f"    {'TOTAL':>12}: {sum(stats.values()):>6}")


def show_status():
    """Show detailed extraction status."""
    total_files = len(list(PDF_DIR.glob("*.pdf"))) if PDF_DIR.exists() else 0
    total_texts = len(list(TEXT_DIR.glob("*.json"))) if TEXT_DIR.exists() else 0
    total_probs = len(list(PROBLEM_DIR.glob("*.json"))) if PROBLEM_DIR.exists() else 0
    remaining = total_files - total_texts

    # Sample problematic files to see actual types
    prob_types = {}
    if PROBLEM_DIR.exists():
        for f in list(PROBLEM_DIR.glob("*.json"))[:100]:
            try:
                data = json.loads(f.read_text())
                ft = data.get("file_type", data.get("problem", "unknown"))
                prob_types[ft] = prob_types.get(ft, 0) + 1
            except:
                pass

    print(f"""
  ╔═══════════════════════════════════════════════════════╗
  ║  Court Decisions — Extraction Status                 ║
  ╠═══════════════════════════════════════════════════════╣
  ║  Total files:      {total_files:>8}                            ║
  ║  Good texts:       {total_texts:>8}  ({total_texts*100//total_files if total_files else 0}%)                       ║
  ║  Problematic:      {total_probs:>8}                            ║
  ║  Remaining:        {remaining:>8}                            ║
  ╚═══════════════════════════════════════════════════════╝""")

    if prob_types:
        print(f"\n  Problematic file types (sample of 100):")
        for ft, count in sorted(prob_types.items(), key=lambda x: -x[1]):
            print(f"    {ft:>20}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Re-extract text from all court decision files")
    parser.add_argument("--all", action="store_true", help="Process all files (skip existing good texts)")
    parser.add_argument("--force", action="store_true", help="Force re-extract everything")
    parser.add_argument("--status", action="store_true", help="Show extraction status")
    parser.add_argument("--rename", action="store_true", help="Show file type distribution")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.rename:
        rename_files()
        return

    if args.force:
        reextract("force")
    elif args.all:
        reextract("all")
    else:
        reextract("problematic")

    show_status()


if __name__ == "__main__":
    main()
