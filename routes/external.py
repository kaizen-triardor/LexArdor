"""External AI and conversation verification endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from db.models import get_chat_messages, add_message
from llm.external import (
    anonymize_prompt, deanonymize_response, query_external,
    PROVIDERS as EXTERNAL_PROVIDERS,
)
from routes.schemas import ExternalAIRequest, VerifyConversationRequest
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["external"])


@router.get("/external/providers")
def list_external_providers():
    """List available external AI providers and their models."""
    return {k: {"name": v["name"], "models": v["models"], "default_model": v["default_model"]}
            for k, v in EXTERNAL_PROVIDERS.items()}


@router.post("/external/anonymize")
def anonymize_text(req: ExternalAIRequest, user: dict = Depends(get_current_user)):
    """Preview anonymization without sending to AI."""
    result = anonymize_prompt(req.prompt, req.names_to_hide)
    return {
        "anonymized": result.anonymized_text,
        "replacements": result.replacements,
        "replacement_count": len(result.replacements),
    }


@router.post("/external/query")
def external_query(req: ExternalAIRequest, user: dict = Depends(get_current_user)):
    """Send anonymized query to external AI provider."""
    # Anonymize if requested
    prompt = req.prompt
    replacements = {}
    if req.anonymize:
        anon = anonymize_prompt(req.prompt, req.names_to_hide)
        prompt = anon.anonymized_text
        replacements = anon.replacements

    system = ("Ti si pravni asistent. Odgovaraj na srpskom jeziku. "
              "Budi precizan i navedi relevantne pravne osnove.")

    try:
        raw_answer = query_external(req.provider, req.api_key, prompt,
                                    model=req.model, system=system)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Greška pri komunikaciji sa {req.provider}: {str(e)}")

    # De-anonymize response
    answer = deanonymize_response(raw_answer, replacements) if replacements else raw_answer

    return {
        "answer": answer,
        "provider": req.provider,
        "model": req.model or EXTERNAL_PROVIDERS.get(req.provider, {}).get("default_model", ""),
        "anonymized": req.anonymize,
        "replacements_made": len(replacements),
        "anonymized_prompt": prompt if req.anonymize else None,
    }


@router.post("/verify-conversation")
def verify_conversation(req: VerifyConversationRequest, user: dict = Depends(get_current_user)):
    """Send the current conversation to an external AI for verification.

    The external AI receives the user's question + local AI's answer + cited sources,
    and provides an independent verification and enhanced analysis.
    """
    # Get conversation messages
    messages = get_chat_messages(req.chat_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Konverzacija nije pronađena")

    # Build verification prompt from conversation
    conv_parts = []
    last_sources = []
    for msg in messages:
        if msg["role"] == "user":
            conv_parts.append(f"PITANJE KORISNIKA:\n{msg['content']}")
        else:
            conv_parts.append(f"ODGOVOR LOKALNOG AI:\n{msg['content']}")
            # Extract sources if available
            if msg.get("sources"):
                try:
                    import json as _json
                    src = _json.loads(msg["sources"]) if isinstance(msg["sources"], str) else msg["sources"]
                    last_sources = src
                except Exception:
                    pass

    # Add source citations to context
    if last_sources:
        conv_parts.append("\nCITIRANI PRAVNI IZVORI:")
        for s in last_sources[:5]:
            law = s.get("law", "")
            article = s.get("article", "")
            text = s.get("text", s.get("full_text", ""))[:300]
            conv_parts.append(f"- {law}, Član {article}: {text}")

    conversation_text = "\n\n".join(conv_parts)

    system = """Ti si nezavisni pravni ekspert koji verifikuje odgovore AI pravnog asistenta za srpsko pravo.

TVOJ ZADATAK:
1. Analiziraj pitanje korisnika i odgovor lokalnog AI.
2. Proveri da li su citirani članovi zakona tačni i relevantni.
3. Identifikuj eventualne greške, propuste ili netačnosti u odgovoru.
4. Daj svoj nezavisni pravni zaključak.
5. Oceni pouzdanost originalnog odgovora (visoka/srednja/niska).

FORMAT:
VERIFIKACIJA: (Da li je originalni odgovor tačan?)
KOREKCIJE: (Ako postoje greške, navedi ih)
DOPUNA: (Dodatne informacije koje lokalni AI nije pomenuo)
OCENA POUZDANOSTI: (Visoka/Srednja/Niska sa obrazloženjem)
FINALNI ZAKLJUČAK: (Tvoj nezavisni pravni zaključak)

Odgovaraj na srpskom jeziku. Budi precizan i navedi relevantne pravne osnove."""

    # Anonymize if requested
    prompt = conversation_text
    replacements = {}
    if req.anonymize:
        anon = anonymize_prompt(conversation_text, req.names_to_hide)
        prompt = anon.anonymized_text
        replacements = anon.replacements

    try:
        raw_answer = query_external(req.provider, req.api_key, prompt,
                                    model=req.model, system=system)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Greška: {str(e)}")

    answer = deanonymize_response(raw_answer, replacements) if replacements else raw_answer

    # Save verification as a special message in the chat
    add_message(req.chat_id, "assistant", f"[VERIFIKACIJA — {req.provider.upper()}]\n\n{answer}",
                confidence="high")

    return {
        "answer": answer,
        "provider": req.provider,
        "model": req.model or EXTERNAL_PROVIDERS.get(req.provider, {}).get("default_model", ""),
        "anonymized": req.anonymize,
        "replacements_made": len(replacements),
    }
