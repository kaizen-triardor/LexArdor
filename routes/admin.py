"""Admin, health, models, and engine config endpoints."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from core.config import settings
from llm.ollama import OllamaClient
from rag.store import get_stats as corpus_stats

router = APIRouter(prefix="/api", tags=["admin"])


def _get_bm25_status() -> dict:
    try:
        from rag.bm25 import get_bm25_index
        return get_bm25_index().status()
    except Exception:
        return {"ready": False, "doc_count": 0}


@router.get("/health")
def health():
    client = OllamaClient()
    ollama_ok = client.is_available()
    try:
        stats = corpus_stats()
    except Exception:
        stats = {"total_articles": 0}
    return {
        "ok": True,
        "ollama_available": ollama_ok,
        "corpus_stats": stats,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/models")
def list_models():
    client = OllamaClient()
    return {"models": client.list_models()}


@router.get("/app-info")
def app_info():
    return {
        "version": settings.app_version,
        "installation_id": settings.installation_id,
        "license_firm": settings.license_firm,
        "support_email": settings.support_email,
    }


@router.get("/admin/engine")
def get_engine_config():
    """Return current engine configuration, models, and capabilities."""
    from db.legal_schema import get_corpus_summary
    from llm.model_router import get_available_models, get_active_reasoning_model, get_active_verifier_model
    corpus = get_corpus_summary()
    models = get_available_models()
    active_reasoning = get_active_reasoning_model()
    active_verifier = get_active_verifier_model()
    return {
        "models": {
            "fast": {"name": "Qwen 3.5 9B Q8", "role": "Brzi odgovori"},
            "reasoning": {"name": active_reasoning["name"], "key": active_reasoning["key"], "role": "Pravna analiza"},
            "verifier": {"name": active_verifier["name"], "key": active_verifier["key"], "role": "Verifikacija citata"},
            "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        },
        "available_models": models,
        "capabilities": {
            "reranker_enabled": True,
            "bm25_enabled": True,
            "citation_verification": True,
            "cross_reference_expansion": True,
            "answer_modes": ["strict", "balanced", "citizen"],
            "multi_model_pipeline": True,
        },
        "bm25": _get_bm25_status(),
        "corpus": corpus,
    }


@router.post("/admin/set-model")
def set_active_model(role: str = Query(...), model_key: str = Query(...)):
    """Change the active model for a role (reasoning or verifier).
    Updates config and optionally swaps the running llama-server model."""
    from llm.model_router import MODELS
    if role not in ("reasoning", "verifier"):
        raise HTTPException(status_code=400, detail="Role must be 'reasoning' or 'verifier'")
    if model_key not in MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {model_key}")
    if role == "reasoning":
        settings.active_reasoning_model = model_key
    elif role == "verifier":
        settings.active_verifier_model = model_key
    return {"ok": True, "role": role, "model": model_key, "name": MODELS[model_key]["name"]}


@router.post("/admin/swap-model")
def swap_running_model(model_key: str = Query(...)):
    """Swap the currently loaded llama-server model. This restarts the server (~30-60s)."""
    from llm.model_router import swap_model as do_swap, MODELS
    if model_key not in MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {model_key}")
    result = do_swap(model_key)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Swap failed"))
    return result


@router.get("/admin/current-model")
def get_current_running_model():
    """Return info about the currently loaded llama-server model."""
    from llm.model_router import get_current_model, detect_loaded_model
    detect_loaded_model()
    current = get_current_model()
    if current:
        return {"loaded": True, **current}
    return {"loaded": False, "message": "No model currently loaded"}


@router.post("/admin/rebuild-bm25")
def admin_rebuild_bm25():
    """Force rebuild the BM25 lexical search index from ChromaDB corpus."""
    from rag.bm25 import rebuild_bm25_index
    result = rebuild_bm25_index()
    if not result["ok"]:
        raise HTTPException(status_code=500, detail="BM25 index build failed")
    return result


@router.get("/admin/bm25-status")
def bm25_status():
    """Return BM25 index status."""
    from rag.bm25 import get_bm25_index
    idx = get_bm25_index()
    return idx.status()


@router.get("/admin/diagnostics")
def query_diagnostics(limit: int = Query(50)):
    """Return query performance diagnostics — recent queries, response times, confidence stats, citation accuracy."""
    from db.models import get_query_logs, get_query_diagnostics
    recent = get_query_logs(limit=limit)
    stats = get_query_diagnostics()
    return {
        "recent_queries": recent,
        **stats,
    }
