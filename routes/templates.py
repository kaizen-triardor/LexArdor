"""Template and draft endpoints, including export."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile, Form
from fastapi.responses import HTMLResponse, Response

from core.doc_extractor import extract_text
from core.templates import (
    analyze_document, fill_template, smart_fill_from_text, validate_document,
    generate_docx, generate_pdf_html,
)
from db.models import (
    create_template, get_template, get_user_templates,
    update_template as db_update_template,
    delete_template as db_delete_template,
    create_draft, get_draft, get_user_drafts,
    update_draft, delete_draft as db_delete_draft,
)
from llm.ollama import OllamaClient
from rag.store import search_with_filters
from routes.schemas import (
    SmartFillRequest, ValidateRequest, DraftCreate, DraftUpdate,
    AnalyzeRiskRequest, CheckCompletenessRequest, LegalBasisRequest, ExplainClauseRequest,
)
from routes.deps import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["templates"])


# ── Template CRUD ────────────────────────────────────────────────────────────

@router.get("/templates")
def list_templates(user: dict = Depends(get_current_user)):
    return get_user_templates(user["id"])


@router.post("/templates")
async def create_template_endpoint(
    file: UploadFile = File(None),
    example_text: str = Form(None),
    name: str = Form(...),
    doc_type: str = Form("ostalo"),
    user: dict = Depends(get_current_user),
):
    """Create template from uploaded file (PDF/DOCX) or pasted text.
    Accepts multipart/form-data with file upload OR example_text field.
    """
    # Extract text from file or use provided text
    if file and file.filename:
        file_bytes = await file.read()
        try:
            text = extract_text(file_bytes, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if len(text.strip()) < 50:
            raise HTTPException(status_code=400, detail="Dokument je prazan ili ima premalo teksta.")
    elif example_text and example_text.strip():
        text = example_text.strip()
    else:
        raise HTTPException(status_code=400, detail="Priložite dokument (PDF/DOCX) ili unesite tekst.")

    llm = OllamaClient()
    if not llm.is_available():
        raise HTTPException(status_code=503, detail="AI model nije dostupan")
    result = analyze_document(text, llm)
    tid = create_template(
        user_id=user["id"],
        name=name,
        doc_type=doc_type,
        body_template=result["body_template"],
        fields=result["fields"],
        example_values=result["example_values"],
    )
    template = get_template(tid)
    return template


@router.get("/templates/{template_id}")
def get_template_endpoint(template_id: int, user: dict = Depends(get_current_user)):
    template = get_template(template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.put("/templates/{template_id}")
def update_template_endpoint(template_id: int, body: dict,
                             user: dict = Depends(get_current_user)):
    """Partial update of a template (name, doc_type, fields)."""
    template = get_template(template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    allowed = {"name", "doc_type", "fields"}
    updates = {k: v for k, v in body.items() if k in allowed and v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    db_update_template(template_id, **updates)
    return get_template(template_id)


@router.delete("/templates/{template_id}")
def delete_template_endpoint(template_id: int, user: dict = Depends(get_current_user)):
    template = get_template(template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    db_delete_template(template_id)
    return {"ok": True}


# ── AI operations on templates ───────────────────────────────────────────────

@router.post("/templates/{template_id}/smart-fill")
def smart_fill_endpoint(template_id: int, req: SmartFillRequest,
                        user: dict = Depends(get_current_user)):
    """AI extracts field values from a free-text description."""
    template = get_template(template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    llm = OllamaClient()
    if not llm.is_available():
        raise HTTPException(status_code=503, detail="AI model is not available")
    values = smart_fill_from_text(req.description, template["fields"], llm)
    return {"field_values": values}


@router.post("/templates/{template_id}/validate")
def validate_endpoint(template_id: int, req: ValidateRequest,
                      user: dict = Depends(get_current_user)):
    """Validate field values against template field definitions."""
    template = get_template(template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    issues = validate_document(req.field_values, template["fields"])
    return {"issues": issues, "valid": len([i for i in issues if i["level"] == "error"]) == 0}


# ── Draft CRUD ───────────────────────────────────────────────────────────────

@router.get("/drafts")
def list_drafts(user: dict = Depends(get_current_user)):
    return get_user_drafts(user["id"])


@router.post("/drafts")
def create_draft_endpoint(req: DraftCreate, user: dict = Depends(get_current_user)):
    template = get_template(req.template_id)
    if not template or template["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Template not found")
    did = create_draft(user["id"], req.template_id, req.name, req.field_values)
    return get_draft(did)


@router.get("/drafts/{draft_id}")
def get_draft_endpoint(draft_id: int, user: dict = Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft or draft["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.put("/drafts/{draft_id}")
def update_draft_endpoint(draft_id: int, req: DraftUpdate,
                          user: dict = Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft or draft["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Draft not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        update_draft(draft_id, **updates)
    return get_draft(draft_id)


@router.delete("/drafts/{draft_id}")
def delete_draft_endpoint(draft_id: int, user: dict = Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft or draft["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Draft not found")
    db_delete_draft(draft_id)
    return {"ok": True}


# ── Export endpoints ─────────────────────────────────────────────────────────

def _get_filled_document(draft_id: int, user: dict, script: str = None) -> tuple[str, dict, dict]:
    """Helper: get filled document text, draft, and template.
    script: 'cyrillic' or 'latin' -- converts output accordingly.
    """
    from core.transliterate import to_latin, to_cyrillic, detect_script
    draft = get_draft(draft_id)
    if not draft or draft["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Draft not found")
    template = get_template(draft["template_id"])
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    filled = fill_template(template["body_template"], draft["field_values"])
    # Convert script if requested
    if script == "cyrillic" and detect_script(filled) == "latin":
        filled = to_cyrillic(filled)
    elif script == "latin" and detect_script(filled) == "cyrillic":
        filled = to_latin(filled)
    return filled, draft, template


@router.post("/drafts/{draft_id}/preview")
def preview_draft(draft_id: int, script: str = Query("original"),
                  user: dict = Depends(get_current_user)):
    """Return filled document as formatted HTML preview."""
    filled, draft, template = _get_filled_document(draft_id, user,
                                                    script=None if script == "original" else script)
    html = generate_pdf_html(filled, title=draft['name'])
    return HTMLResponse(content=html)


@router.post("/drafts/{draft_id}/export/pdf")
def export_pdf(draft_id: int, script: str = Query("original"),
               user: dict = Depends(get_current_user)):
    """Export as PDF with proper legal formatting."""
    filled, draft, template = _get_filled_document(draft_id, user,
                                                    script=None if script == "original" else script)
    html_str = generate_pdf_html(filled, title=draft['name'])
    filename = f"{draft['name'].replace(' ', '_')}.pdf"
    try:
        from weasyprint import HTML as WeasyprintHTML
        pdf_bytes = WeasyprintHTML(string=html_str).write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ImportError:
        # Fallback: return HTML as downloadable file
        return Response(
            content=html_str.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename.replace(".pdf", ".html")}"'},
        )


@router.post("/drafts/{draft_id}/export/docx")
def export_docx(draft_id: int, script: str = Query("original"),
                user: dict = Depends(get_current_user)):
    """Export as DOCX with proper legal document formatting."""
    filled, draft, template = _get_filled_document(draft_id, user,
                                                    script=None if script == "original" else script)
    try:
        docx_bytes = generate_docx(filled, title=draft["name"])
        filename = f"{draft['name'].replace(' ', '_')}.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="python-docx is not installed")


# ── Drafting Studio Enhancements (Sprint 7) ────────────────────────────────

def _parse_json_response(raw: str) -> dict | list:
    """Extract and parse JSON from LLM response, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
        # Remove closing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    return json.loads(text)


def _ensure_llm() -> OllamaClient:
    """Create OllamaClient and verify availability."""
    llm = OllamaClient()
    if not llm.is_available():
        raise HTTPException(status_code=503, detail="AI model nije dostupan")
    return llm


# Feature 16: Clause Risk Detection
@router.post("/drafts/analyze-risk")
def analyze_risk(req: AnalyzeRiskRequest, user: dict = Depends(get_current_user)):
    """Analyze contract text for risky clauses."""
    llm = _ensure_llm()

    system_prompt = (
        "Ti si pravni analitičar. Analiziraj tekst ugovora i identifikuj rizične klauzule. "
        "Za svaku rizičnu klauzulu navedi: tačan tekst klauzule, nivo rizika (high, medium, low), "
        "i objašnjenje zašto je rizična. "
        "Odgovori ISKLJUČIVO u JSON formatu: "
        '{\"risks\": [{\"clause\": \"...\", \"risk_level\": \"high|medium|low\", \"explanation\": \"...\"}]}'
    )

    user_prompt = (
        f"Tip dokumenta: {req.doc_type}\n\n"
        f"Tekst za analizu:\n{req.text}"
    )

    try:
        raw = llm.generate(user_prompt, system=system_prompt, max_tokens=4096)
        parsed = _parse_json_response(raw)
        if isinstance(parsed, dict) and "risks" in parsed:
            risks = parsed["risks"]
        elif isinstance(parsed, list):
            risks = parsed
        else:
            risks = []
        # Validate risk_level values
        valid_levels = {"high", "medium", "low"}
        for r in risks:
            if r.get("risk_level") not in valid_levels:
                r["risk_level"] = "medium"
        return {"risks": risks}
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("Failed to parse risk analysis response: %s", e)
        return {"risks": [], "raw_response": raw}


# Feature 17: Missing Clause Detection
@router.post("/drafts/check-completeness")
def check_completeness(req: CheckCompletenessRequest, user: dict = Depends(get_current_user)):
    """Check for missing standard clauses based on document type."""
    llm = _ensure_llm()

    system_prompt = (
        "Ti si pravni ekspert za srpsko pravo. Na osnovu tipa dokumenta i teksta, "
        "identifikuj standardne klauzule koje nedostaju. "
        "Za svaku klauzulu koja nedostaje navedi: naziv klauzule, važnost (critical, recommended, optional), "
        "i objašnjenje zašto je potrebna. Takođe oceni kompletnost dokumenta od 0.0 do 1.0. "
        "Odgovori ISKLJUČIVO u JSON formatu: "
        '{\"missing\": [{\"clause_name\": \"...\", \"importance\": \"critical|recommended|optional\", '
        '\"explanation\": \"...\"}], \"completeness_score\": 0.0}'
    )

    user_prompt = (
        f"Tip dokumenta: {req.doc_type}\n\n"
        f"Tekst dokumenta:\n{req.text}"
    )

    try:
        raw = llm.generate(user_prompt, system=system_prompt, max_tokens=4096)
        parsed = _parse_json_response(raw)
        missing = parsed.get("missing", [])
        score = parsed.get("completeness_score", 0.0)
        # Validate importance values
        valid_importance = {"critical", "recommended", "optional"}
        for m in missing:
            if m.get("importance") not in valid_importance:
                m["importance"] = "recommended"
        # Clamp score
        score = max(0.0, min(1.0, float(score)))
        return {"missing": missing, "completeness_score": score}
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("Failed to parse completeness response: %s", e)
        return {"missing": [], "completeness_score": 0.0, "raw_response": raw}


# Feature 18: Legal Basis Suggestions Per Draft
@router.post("/drafts/legal-basis")
def legal_basis_suggestions(req: LegalBasisRequest, user: dict = Depends(get_current_user)):
    """Extract legal concepts from text and find relevant law articles via RAG."""
    llm = _ensure_llm()

    # Step 1: Extract key legal concepts from the text
    system_prompt = (
        "Iz datog pravnog teksta izvuci ključne pravne koncepte i pojmove "
        "koji bi mogli imati zakonski osnov u srpskom pravu. "
        "Odgovori ISKLJUČIVO u JSON formatu: "
        '{\"concepts\": [\"koncept1\", \"koncept2\", ...]}'
    )

    try:
        raw = llm.generate(req.text, system=system_prompt, max_tokens=1024)
        parsed = _parse_json_response(raw)
        concepts = parsed.get("concepts", [])
    except (json.JSONDecodeError, ValueError):
        # Fallback: split text into sentences and use first few as concepts
        concepts = [s.strip() for s in req.text.split(".") if len(s.strip()) > 20][:5]

    # Step 2: Search RAG for each concept
    suggestions = []
    for concept in concepts[:10]:  # Limit to 10 concepts
        try:
            results = search_with_filters(concept, top_k=3, doc_types=["zakon", "uredba", "pravilnik"])
            articles = []
            for r in results:
                meta = r.get("metadata", {})
                articles.append({
                    "law": meta.get("law_title", meta.get("source", "Nepoznat zakon")),
                    "article": meta.get("article_number", meta.get("chunk_id", "")),
                    "text_preview": r.get("text", "")[:200],
                })
            if articles:
                suggestions.append({
                    "concept": concept,
                    "articles": articles,
                })
        except Exception as e:
            log.warning("RAG search failed for concept '%s': %s", concept, e)
            continue

    return {"suggestions": suggestions}


# Feature 19: Clause Explain/Rewrite
@router.post("/drafts/explain-clause")
def explain_clause(req: ExplainClauseRequest, user: dict = Depends(get_current_user)):
    """Explain, simplify, or formalize a clause."""
    llm = _ensure_llm()

    action_prompts = {
        "explain": (
            "Ti si pravni savetnik. Objasni sledeću klauzulu detaljno. "
            "Objasni šta znači, koje su pravne posledice, i na šta treba obratiti pažnju. "
            "Odgovori na srpskom jeziku, jasno i razumljivo."
        ),
        "simplify": (
            "Ti si pravni savetnik koji piše za građane. Prepiši sledeću klauzulu "
            "jednostavnim, razumljivim jezikom, bez pravničkog žargona. "
            "Zadrži isto pravno značenje ali koristi svakodnevni srpski jezik."
        ),
        "formalize": (
            "Ti si pravni stručnjak za formalne pravne dokumente. Prepiši sledeću klauzulu "
            "koristeći formalan pravnički jezik, precizne pravne termine i standardnu "
            "strukturu srpskih pravnih dokumenata. Klauzula treba da bude pravno precizna."
        ),
    }

    if req.action not in action_prompts:
        raise HTTPException(
            status_code=400,
            detail=f"Nevažeća akcija: {req.action}. Dozvoljene: explain, simplify, formalize",
        )

    system_prompt = action_prompts[req.action]

    try:
        result = llm.generate(req.text, system=system_prompt, max_tokens=2048)
        return {"result": result.strip(), "action": req.action}
    except Exception as e:
        log.error("Clause explain/rewrite failed: %s", e)
        raise HTTPException(status_code=500, detail="AI obrada nije uspela")
