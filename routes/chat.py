"""Chat history endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from db.models import (
    create_chat, get_chat_messages, get_user_chats, delete_chat,
)
from routes.schemas import ChatCreate
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/chats")
def list_chats(user: dict = Depends(get_current_user)):
    return get_user_chats(user["id"])


@router.post("/chats")
def create_chat_endpoint(req: ChatCreate, user: dict = Depends(get_current_user)):
    chat_id = create_chat(user["id"], req.title)
    return {"id": chat_id, "title": req.title}


@router.get("/chats/{chat_id}/messages")
def get_messages(chat_id: int, user: dict = Depends(get_current_user)):
    return get_chat_messages(chat_id)


@router.delete("/chats/{chat_id}")
def delete_chat_endpoint(chat_id: int, user: dict = Depends(get_current_user)):
    delete_chat(chat_id)
    return {"ok": True}


@router.post("/chats/{chat_id}/export")
def export_chat(chat_id: int, format: str = Query("pdf"),
                user: dict = Depends(get_current_user)):
    """Export chat conversation as PDF, DOCX, or HTML document."""
    messages = get_chat_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Chat not found or empty")

    # Build document text
    lines = ["LEXARDOR — PRAVNA ANALIZA", "=" * 40, ""]
    for msg in messages:
        role = "PITANJE" if msg["role"] == "user" else "ODGOVOR"
        lines.append(f"{role}:")
        lines.append(msg["content"])
        if msg["role"] == "assistant" and msg.get("sources"):
            lines.append("")
            lines.append("IZVORI:")
            for s in (msg["sources"] or [])[:5]:
                law = s.get("law", "")
                art = s.get("article", "")
                lines.append(f"  - {law}, Član {art}")
        lines.append("")
        lines.append("-" * 40)
        lines.append("")

    doc_text = "\n".join(lines)

    if format == "html":
        from core.templates import generate_pdf_html
        html = generate_pdf_html(doc_text, title="LexArdor — Pravna analiza")
        return Response(content=html, media_type="text/html",
                       headers={"Content-Disposition": "attachment; filename=lexardor-chat.html"})
    elif format == "docx":
        from core.templates import generate_docx
        docx_bytes = generate_docx(doc_text, title="LexArdor — Pravna analiza")
        return Response(content=docx_bytes,
                       media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       headers={"Content-Disposition": "attachment; filename=lexardor-chat.docx"})
    else:  # pdf (HTML fallback)
        from core.templates import generate_pdf_html
        html = generate_pdf_html(doc_text, title="LexArdor — Pravna analiza")
        try:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html).write_pdf()
            return Response(content=pdf_bytes, media_type="application/pdf",
                           headers={"Content-Disposition": "attachment; filename=lexardor-chat.pdf"})
        except ImportError:
            return Response(content=html, media_type="text/html",
                           headers={"Content-Disposition": "attachment; filename=lexardor-chat.html"})
