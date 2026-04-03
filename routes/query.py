"""Query endpoints (RAG)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.config import settings
from db.models import (
    get_user, create_chat, add_message, get_chat_messages, update_chat_title,
)
from rag.pipeline import query as rag_query, query_stream as rag_query_stream
from routes.schemas import QueryRequest
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query")
def query_endpoint(req: QueryRequest, user: dict = Depends(get_current_user)):
    import time as _time
    t0 = _time.time()

    # Create or reuse chat
    chat_id = req.chat_id
    if not chat_id:
        chat_id = create_chat(user["id"])
    # Save user message
    add_message(chat_id, "user", req.query)
    # Get chat history for conversation context
    chat_history = get_chat_messages(chat_id)[:-1]
    # Run RAG — use multi-stage pipeline for deep analysis
    if req.deep_analysis:
        from rag.multi_stage import query_deep
        result = query_deep(req.query, top_k=max(req.top_k, 8),
                            chat_history=chat_history, answer_mode=req.answer_mode,
                            reference_date=req.reference_date)
    else:
        result = rag_query(req.query, top_k=req.top_k, use_heavy_model=req.heavy_model,
                           short_answer=req.short_answer, chat_history=chat_history,
                           answer_mode=req.answer_mode,
                           reference_date=req.reference_date,
                           doc_types=req.doc_types,
                           min_authority=req.min_authority)
    # Save assistant message
    add_message(chat_id, "assistant", result["answer"],
                sources=result["sources"], confidence=result["confidence"])
    # Auto-title on first message
    if not req.chat_id:
        title = req.query[:60] + ("..." if len(req.query) > 60 else "")
        update_chat_title(chat_id, title)

    # Log query diagnostics
    elapsed_ms = int((_time.time() - t0) * 1000)
    try:
        from db.models import log_query
        conf = result.get("confidence", {})
        conf_level = conf.get("level", conf) if isinstance(conf, dict) else conf
        cites = result.get("citations", {})
        log_query(
            query=req.query, answer_mode=req.answer_mode,
            confidence=conf_level,
            source_count=len(result.get("sources", [])),
            citation_verified=cites.get("verified_count", 0),
            citation_flagged=cites.get("flagged_count", 0),
            model_used=result.get("model_used", ""),
            bm25_used=bool(result.get("diagnostics", {}).get("bm25_used")),
            response_time_ms=elapsed_ms,
            multi_stage=req.deep_analysis,
        )
    except Exception:
        pass  # Logging should never break the query

    return {
        "answer": result["answer"],
        "structured_answer": result.get("structured_answer"),
        "sources": result["sources"],
        "confidence": result["confidence"],
        "chat_id": chat_id,
        "model_used": result["model_used"],
        "answer_mode": result.get("answer_mode", "balanced"),
        "citations": result.get("citations"),
        "answer_spans": result.get("answer_spans"),
        "diagnostics": result.get("diagnostics"),
        "response_time_ms": elapsed_ms,
    }


@router.get("/query/stream")
def query_stream_endpoint(
    q: str = Query(...),
    chat_id: int | None = Query(None),
    top_k: int = Query(5),
    heavy: bool = Query(False),
    short: bool = Query(False),
    token: str = Query(None),
    mode: str = Query("balanced"),
    ref_date: str | None = Query(None),
    doc_types: str | None = Query(None),
):
    user = get_user(settings.default_admin_user)
    if not user:
        raise HTTPException(status_code=500, detail="Default user not found")

    cid = chat_id
    if not cid:
        cid = create_chat(user["id"])
    add_message(cid, "user", q)

    chat_history = get_chat_messages(cid)[:-1] if cid else []
    parsed_doc_types = doc_types.split(",") if doc_types else None
    result = rag_query_stream(q, top_k=top_k, use_heavy_model=heavy,
                              short_answer=short, chat_history=chat_history,
                              answer_mode=mode, reference_date=ref_date,
                              doc_types=parsed_doc_types)
    stream = result["stream"]
    sources = result["sources"]
    confidence = result["confidence"]
    model_used = result["model_used"]

    import time as _time
    stream_t0 = _time.time()

    def event_generator():
        full_answer = []
        for token_text in stream:
            full_answer.append(token_text)
            yield f"data: {json.dumps({'token': token_text})}\n\n"
        # Final event with sources, citations, and metadata
        from rag.reasoning import verify_citations
        answer = "".join(full_answer)
        citations = verify_citations(answer, sources)
        yield f"data: {json.dumps({'done': True, 'sources': sources, 'confidence': confidence, 'chat_id': cid, 'model_used': model_used, 'citations': citations})}\n\n"
        add_message(cid, "assistant", answer, sources=sources, confidence=confidence)
        if not chat_id:
            title = q[:60] + ("..." if len(q) > 60 else "")
            update_chat_title(cid, title)
        # Log query diagnostics for stream endpoint
        try:
            from db.models import log_query
            elapsed_ms = int((_time.time() - stream_t0) * 1000)
            conf_level = confidence if isinstance(confidence, str) else (confidence.get("level", "") if isinstance(confidence, dict) else str(confidence))
            log_query(
                query=q, answer_mode=mode, confidence=conf_level,
                source_count=len(sources or []),
                citation_verified=citations.get("verified_count", 0) if isinstance(citations, dict) else 0,
                citation_flagged=citations.get("flagged_count", 0) if isinstance(citations, dict) else 0,
                model_used=model_used or "",
                bm25_used=False,
                response_time_ms=elapsed_ms,
                multi_stage=False,
            )
        except Exception:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/query/research")
def research_endpoint(body: dict, user: dict = Depends(get_current_user)):
    """Autonomous research agent -- multi-query deep research on a topic."""
    topic = body.get("topic", "")
    if not topic or len(topic) < 5:
        raise HTTPException(status_code=400, detail="Research topic too short")
    from rag.agent import research
    result = research(topic, max_queries=body.get("max_queries", 5),
                      reference_date=body.get("reference_date"))
    return result


@router.post("/query/{chat_id}/verify-citations")
def verify_chat_citations(chat_id: int, user: dict = Depends(get_current_user)):
    """Run LLM-powered citation verification on the last assistant message."""
    messages = get_chat_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Chat not found or empty")

    # Find last assistant message with sources
    last_answer = None
    last_sources = None
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            last_answer = msg["content"]
            last_sources = msg.get("sources", [])
            break

    if not last_answer:
        raise HTTPException(status_code=404, detail="No assistant message found")

    from rag.reasoning import verify_citations_with_llm
    from llm.ollama import OllamaClient

    client = OllamaClient()
    if not client.is_available():
        raise HTTPException(status_code=503, detail="AI model nije dostupan")

    result = verify_citations_with_llm(last_answer, last_sources or [], client)
    return result
