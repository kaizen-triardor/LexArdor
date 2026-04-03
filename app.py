"""LexArdor v2 — FastAPI backend server."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

from core.config import settings
from db.init import setup_database


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_database()
    # Detect which model is already loaded in llama-server (started by start.sh)
    from llm.model_router import detect_loaded_model
    detect_loaded_model()
    # Build BM25 lexical search index — load from disk synchronously on startup
    # so search works immediately. Disk load takes ~20-25s for 1.35 GB index.
    # If no persisted index exists, build in background (takes ~3 min).
    try:
        from rag.bm25 import build_bm25_index, get_bm25_index
        import logging
        _log = logging.getLogger("lexardor")
        result = build_bm25_index(background=False)  # sync: block until loaded
        if result.get("ok"):
            _log.info("BM25 ready: %d docs (source: %s)", result.get("doc_count", 0), result.get("source", "built"))
        else:
            # Disk load failed — rebuild in background so app doesn't block forever
            _log.warning("BM25 disk load failed, rebuilding in background...")
            build_bm25_index(background=True)
    except Exception as e:
        import logging
        logging.getLogger("lexardor").warning("BM25 init skipped: %s", e)
    yield


app = FastAPI(title="LexArdor — AI pravni asistent", version="2.0.0", lifespan=lifespan)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ─────────────────────────────────────────────────────────

from routes.query import router as query_router
from routes.chat import router as chat_router
from routes.documents import router as docs_router
from routes.corpus import router as corpus_router
from routes.templates import router as tpl_router
from routes.external import router as ext_router
from routes.admin import router as admin_router
from routes.support import router as support_router
from routes.matters import router as matters_router

app.include_router(query_router)
app.include_router(chat_router)
app.include_router(docs_router)
app.include_router(corpus_router)
app.include_router(tpl_router)
app.include_router(ext_router)
app.include_router(admin_router)
app.include_router(support_router)
app.include_router(matters_router)


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.get("/favicon.png")
def serve_favicon():
    from pathlib import Path
    favicon_path = Path(__file__).parent / "favicon.png"
    if favicon_path.exists():
        return FileResponse(favicon_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    try:
        from pathlib import Path
        html_path = Path(__file__).parent / "templates" / "index.html"
        return html_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HTMLResponse(
            "<h1>LexArdor v2</h1><p>Frontend not built yet. API is running at /api/</p>",
            status_code=200,
        )
