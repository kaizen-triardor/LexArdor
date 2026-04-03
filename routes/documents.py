"""Client document endpoints."""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form

from core.doc_extractor import extract_text
from rag.store import ingest_client_document, list_client_documents, delete_client_document
from routes.schemas import DocumentUpload
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["documents"])

ALLOWED_DOC_CATEGORIES = {"zakon", "komentar", "praksa", "ostalo"}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}


@router.post("/documents/upload")
def upload_document(req: DocumentUpload, user: dict = Depends(get_current_user)):
    """Upload a client document (JSON body with title + content)."""
    if not req.title.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="Title and content are required")
    meta = {}
    if req.category:
        meta["category"] = req.category
    result = ingest_client_document(req.title, req.content, meta)
    return {"ok": True, "doc_id": result["doc_id"], "chunks": result["chunks"]}


@router.post("/documents/upload-file")
async def upload_document_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("ostalo"),
    user: dict = Depends(get_current_user),
):
    """Upload a client document from a file (PDF, DOCX, TXT)."""
    from pathlib import Path

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="File is required")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Nepodržan format: {ext}. Koristite PDF, DOCX ili TXT.",
        )

    if category not in ALLOWED_DOC_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Nepoznata kategorija: {category}. Dozvoljene: {', '.join(sorted(ALLOWED_DOC_CATEGORIES))}",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        content = extract_text(file_bytes, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not content.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted from the file")

    # Use filename as title if not provided
    doc_title = title.strip() if title.strip() else Path(file.filename).stem

    meta = {"category": category}
    result = ingest_client_document(doc_title, content, meta)
    return {
        "ok": True,
        "doc_id": result["doc_id"],
        "chunks": result["chunks"],
        "title": doc_title,
        "category": category,
    }


@router.get("/documents")
def list_documents_endpoint(user: dict = Depends(get_current_user)):
    """List client's uploaded documents with previews."""
    return list_client_documents(include_preview=True)


@router.get("/documents/{doc_id}")
def get_document_content(doc_id: str, user: dict = Depends(get_current_user)):
    """Get full text content of a client document by fetching all chunks."""
    from rag.store import get_client_collection
    collection = get_client_collection()
    # Client docs have IDs like "doc_id_chunk_0", "doc_id_chunk_1", etc.
    # Try to find all chunks for this doc
    try:
        results = collection.get(
            where={"doc_id": doc_id},
            include=["documents", "metadatas"],
        )
        if results and results["ids"]:
            # Combine all chunks
            chunks = sorted(zip(results["ids"], results["documents"]),
                          key=lambda x: x[0])
            full_text = "\n".join(text for _, text in chunks)
            meta = results["metadatas"][0] if results["metadatas"] else {}
            return {
                "doc_id": doc_id,
                "title": meta.get("title", doc_id),
                "category": meta.get("category", ""),
                "content": full_text,
                "chunk_count": len(chunks),
            }
    except Exception:
        pass
    # Fallback: try getting by ID prefix
    try:
        all_results = collection.get(include=["documents", "metadatas"])
        matching = [(i, d, m) for i, d, m in
                    zip(all_results["ids"], all_results["documents"], all_results["metadatas"])
                    if i.startswith(doc_id)]
        if matching:
            full_text = "\n".join(d for _, d, _ in sorted(matching))
            meta = matching[0][2]
            return {
                "doc_id": doc_id,
                "title": meta.get("title", doc_id),
                "category": meta.get("category", ""),
                "content": full_text,
                "chunk_count": len(matching),
            }
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Document not found")


@router.delete("/documents/{doc_id}")
def delete_document_endpoint(doc_id: str, user: dict = Depends(get_current_user)):
    """Delete a client document."""
    deleted = delete_client_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}
