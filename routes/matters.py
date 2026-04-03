"""Research Workspace — Matters, Notes, and linked items."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.models import (
    create_matter, get_user_matters, get_matter, update_matter, delete_matter,
    add_matter_note, delete_matter_note, link_chat_to_matter, link_doc_to_matter,
)
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["matters"])


class MatterCreate(BaseModel):
    name: str
    description: str = ""


class MatterUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class NoteCreate(BaseModel):
    content: str


class LinkItem(BaseModel):
    item_id: str | int


# ── Matters CRUD ─────────────────────────────────────────────────────────────

@router.get("/matters")
def list_matters(user: dict = Depends(get_current_user)):
    return get_user_matters(user["id"])


@router.post("/matters")
def create_matter_endpoint(req: MatterCreate, user: dict = Depends(get_current_user)):
    matter_id = create_matter(user["id"], req.name, req.description)
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
    delete_matter(matter_id)
    return {"ok": True}


# ── Notes ────────────────────────────────────────────────────────────────────

@router.post("/matters/{matter_id}/notes")
def add_note(matter_id: int, req: NoteCreate, user: dict = Depends(get_current_user)):
    note_id = add_matter_note(matter_id, req.content)
    return {"id": note_id}


@router.delete("/matters/notes/{note_id}")
def remove_note(note_id: int, user: dict = Depends(get_current_user)):
    delete_matter_note(note_id)
    return {"ok": True}


# ── Link chats and documents ────────────────────────────────────────────────

@router.post("/matters/{matter_id}/link-chat")
def link_chat(matter_id: int, req: LinkItem, user: dict = Depends(get_current_user)):
    link_chat_to_matter(matter_id, int(req.item_id))
    return {"ok": True}


@router.post("/matters/{matter_id}/link-doc")
def link_doc(matter_id: int, req: LinkItem, user: dict = Depends(get_current_user)):
    link_doc_to_matter(matter_id, str(req.item_id))
    return {"ok": True}
