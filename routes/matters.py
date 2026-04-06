"""Enhanced Radni Prostor — Cases, Parties, Documents, Events, Notes."""
import os
import json
import logging
from pathlib import Path
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel

from db.models import (
    create_matter, get_user_matters, get_matter, update_matter, delete_matter,
    add_matter_note, delete_matter_note, update_matter_note, toggle_note_pin,
    link_chat_to_matter, link_doc_to_matter,
    unlink_chat_from_matter, unlink_doc_from_matter,
    add_matter_party, delete_matter_party,
    add_matter_file, mark_file_indexed, delete_matter_file,
    add_matter_event, update_matter_event, delete_matter_event,
    get_upcoming_events,
)
from routes.deps import get_current_user
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["matters"])

MATTER_FILES_DIR = Path(settings.db_path).parent / "matter_files"


# ── Request Models ──────────────────────────────────────────────────────────

class MatterCreate(BaseModel):
    name: str
    description: str = ""
    case_number: str = ""
    case_type: str = "ostalo"
    court: str = ""
    judge: str = ""
    client_name: str = ""
    opposing_party: str = ""
    priority: str = "normal"

class MatterUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    case_number: str | None = None
    case_type: str | None = None
    court: str | None = None
    judge: str | None = None
    client_name: str | None = None
    opposing_party: str | None = None
    priority: str | None = None
    tags: list[str] | None = None

class NoteCreate(BaseModel):
    content: str

class NoteUpdate(BaseModel):
    content: str

class LinkItem(BaseModel):
    item_id: str | int

class PartyCreate(BaseModel):
    name: str
    role: str = "ostalo"
    contact: str = ""
    notes: str = ""

class EventCreate(BaseModel):
    title: str
    event_type: str = "ostalo"
    event_date: str  # YYYY-MM-DD
    description: str = ""
    event_time: str = ""
    location: str = ""
    reminder_days: int = 3

class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    event_type: str | None = None
    event_date: str | None = None
    event_time: str | None = None
    location: str | None = None
    reminder_days: int | None = None
    completed: bool | None = None


# ── Matters CRUD ─────────────────────────────────────────────────────────────

@router.get("/matters")
def list_matters(user: dict = Depends(get_current_user)):
    matters = get_user_matters(user["id"])
    # Add next upcoming event for each matter
    for m in matters:
        events = get_upcoming_events(user["id"], days=365)
        m["next_event"] = next(
            (e for e in events if e.get("matter_id") == m["id"]), None
        )
    return matters


@router.post("/matters")
def create_matter_endpoint(req: MatterCreate, user: dict = Depends(get_current_user)):
    matter_id = create_matter(user["id"], req.name, req.description)
    # Update additional fields
    extra = req.model_dump(exclude={"name", "description"}, exclude_none=True)
    extra = {k: v for k, v in extra.items() if v}
    if extra:
        update_matter(matter_id, **extra)
    return {"id": matter_id, "name": req.name}


@router.get("/matters/{matter_id}")
def get_matter_endpoint(matter_id: int, user: dict = Depends(get_current_user)):
    m = get_matter(matter_id)
    if not m:
        raise HTTPException(status_code=404, detail="Predmet nije pronađen")
    return m


@router.put("/matters/{matter_id}")
def update_matter_endpoint(matter_id: int, req: MatterUpdate,
                           user: dict = Depends(get_current_user)):
    update_matter(matter_id, **req.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/matters/{matter_id}")
def delete_matter_endpoint(matter_id: int, user: dict = Depends(get_current_user)):
    # Also delete physical files
    m = get_matter(matter_id)
    if m:
        for f in m.get("files", []):
            try:
                os.remove(f["file_path"])
            except OSError:
                pass
    delete_matter(matter_id)
    return {"ok": True}


# ── Notes ────────────────────────────────────────────────────────────────────

@router.post("/matters/{matter_id}/notes")
def add_note(matter_id: int, req: NoteCreate, user: dict = Depends(get_current_user)):
    note_id = add_matter_note(matter_id, req.content)
    return {"id": note_id}


@router.put("/matters/notes/{note_id}")
def edit_note(note_id: int, req: NoteUpdate, user: dict = Depends(get_current_user)):
    update_matter_note(note_id, req.content)
    return {"ok": True}


@router.delete("/matters/notes/{note_id}")
def remove_note(note_id: int, user: dict = Depends(get_current_user)):
    delete_matter_note(note_id)
    return {"ok": True}


@router.post("/matters/notes/{note_id}/pin")
def pin_note(note_id: int, user: dict = Depends(get_current_user)):
    toggle_note_pin(note_id)
    return {"ok": True}


# ── Link / Unlink chats and documents ──────────────────────────────────────

@router.post("/matters/{matter_id}/link-chat")
def link_chat(matter_id: int, req: LinkItem, user: dict = Depends(get_current_user)):
    link_chat_to_matter(matter_id, int(req.item_id))
    return {"ok": True}


@router.delete("/matters/{matter_id}/unlink-chat/{chat_id}")
def unlink_chat(matter_id: int, chat_id: int, user: dict = Depends(get_current_user)):
    unlink_chat_from_matter(matter_id, chat_id)
    return {"ok": True}


@router.post("/matters/{matter_id}/link-doc")
def link_doc(matter_id: int, req: LinkItem, user: dict = Depends(get_current_user)):
    link_doc_to_matter(matter_id, str(req.item_id))
    return {"ok": True}


@router.delete("/matters/{matter_id}/unlink-doc/{doc_id}")
def unlink_doc(matter_id: int, doc_id: str, user: dict = Depends(get_current_user)):
    unlink_doc_from_matter(matter_id, doc_id)
    return {"ok": True}


# ── Parties ──────────────────────────────────────────────────────────────────

@router.post("/matters/{matter_id}/parties")
def add_party(matter_id: int, req: PartyCreate, user: dict = Depends(get_current_user)):
    pid = add_matter_party(matter_id, req.name, req.role, req.contact, req.notes)
    return {"id": pid}


@router.delete("/matters/parties/{party_id}")
def remove_party(party_id: int, user: dict = Depends(get_current_user)):
    delete_matter_party(party_id)
    return {"ok": True}


# ── File Upload (Document Vault) ─────────────────────────────────────────────

@router.post("/matters/{matter_id}/files")
async def upload_file(matter_id: int,
                      file: UploadFile = File(...),
                      category: str = Form("ostalo"),
                      description: str = Form(""),
                      user: dict = Depends(get_current_user)):
    # Create directory for this matter
    matter_dir = MATTER_FILES_DIR / str(matter_id)
    matter_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    file_path = matter_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    # Detect file type
    ext = Path(file.filename).suffix.lower().lstrip(".")

    fid = add_matter_file(
        matter_id=matter_id,
        filename=file.filename,
        file_type=ext,
        file_size=len(content),
        file_path=str(file_path),
        description=description,
        category=category,
    )

    # Auto-index text-based files into per-matter ChromaDB collection
    if ext in ("pdf", "docx", "txt", "doc", "odt"):
        try:
            _index_file_for_matter(matter_id, fid, str(file_path), ext)
        except Exception as e:
            logger.warning("Auto-indexing failed for file %s: %s", file.filename, e)

    return {"id": fid, "filename": file.filename, "size": len(content)}


@router.delete("/matters/files/{file_id}")
def remove_file(file_id: int, user: dict = Depends(get_current_user)):
    path = delete_matter_file(file_id)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True}


@router.get("/matters/{matter_id}/files/{file_id}/download")
def download_file(matter_id: int, file_id: int, user: dict = Depends(get_current_user)):
    m = get_matter(matter_id)
    if not m:
        raise HTTPException(status_code=404)
    f = next((f for f in m.get("files", []) if f["id"] == file_id), None)
    if not f:
        raise HTTPException(status_code=404)
    content = Path(f["file_path"]).read_bytes()
    return Response(content=content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={f['filename']}"})


def _index_file_for_matter(matter_id: int, file_id: int, file_path: str, ext: str):
    """Extract text and index into per-matter ChromaDB collection."""
    from rag.store import get_or_create_matter_collection, embed_query
    import re

    # Extract text
    text = ""
    if ext == "txt":
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    elif ext == "pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF not installed, cannot index PDF")
            return
    elif ext in ("docx", "doc", "odt"):
        try:
            import docx2txt
            text = docx2txt.process(file_path)
        except ImportError:
            logger.warning("docx2txt not installed, cannot index DOCX")
            return

    if not text.strip():
        return

    # Chunk text (~500 chars with overlap)
    chunks = []
    words = text.split()
    chunk_size = 100  # words
    overlap = 20
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)

    if not chunks:
        return

    # Index into per-matter collection
    collection = get_or_create_matter_collection(matter_id)
    ids = [f"mf_{file_id}_c{i}" for i in range(len(chunks))]
    embeddings = [embed_query(c) for c in chunks]
    metadatas = [{"file_id": file_id, "matter_id": matter_id,
                  "chunk_index": i, "source": Path(file_path).name} for i in range(len(chunks))]

    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    mark_file_indexed(file_id)
    logger.info("Indexed %d chunks from %s into matter_%d collection",
                len(chunks), Path(file_path).name, matter_id)


# ── Events (Timeline) ───────────────────────────────────────────────────────

@router.post("/matters/{matter_id}/events")
def add_event(matter_id: int, req: EventCreate, user: dict = Depends(get_current_user)):
    eid = add_matter_event(
        matter_id=matter_id, title=req.title, event_type=req.event_type,
        event_date=req.event_date, description=req.description,
        event_time=req.event_time, location=req.location,
        reminder_days=req.reminder_days,
    )
    return {"id": eid}


@router.put("/matters/events/{event_id}")
def edit_event(event_id: int, req: EventUpdate, user: dict = Depends(get_current_user)):
    update_matter_event(event_id, **req.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/matters/events/{event_id}")
def remove_event(event_id: int, user: dict = Depends(get_current_user)):
    delete_matter_event(event_id)
    return {"ok": True}


@router.get("/matters/upcoming-events")
def upcoming_events(days: int = 30, user: dict = Depends(get_current_user)):
    return get_upcoming_events(user["id"], days=days)


# ── ICS Calendar Export ──────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/events.ics")
def export_events_ics(matter_id: int, user: dict = Depends(get_current_user)):
    """Export matter events as ICS file for import into any calendar app."""
    m = get_matter(matter_id)
    if not m:
        raise HTTPException(status_code=404)

    events = m.get("events", [])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LexArdor//Radni Prostor//SR",
        f"X-WR-CALNAME:LexArdor - {m['name']}",
    ]
    for e in events:
        dt = e["event_date"].replace("-", "")
        time_str = e.get("event_time", "").replace(":", "")
        if time_str:
            dtstart = f"{dt}T{time_str}00"
        else:
            dtstart = dt
        lines.extend([
            "BEGIN:VEVENT",
            f"DTSTART:{dtstart}",
            f"SUMMARY:{e['title']}",
            f"DESCRIPTION:{e.get('description', '')}",
            f"LOCATION:{e.get('location', '')}",
            f"CATEGORIES:{e.get('event_type', '')}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")

    ics_content = "\r\n".join(lines)
    filename = f"lexardor-{m['name'][:30].replace(' ', '_')}.ics"
    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
