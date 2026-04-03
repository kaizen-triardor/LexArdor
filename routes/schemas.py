"""Pydantic request/response models for all API endpoints."""
from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str
    chat_id: int | None = None
    top_k: int = 5
    heavy_model: bool = False
    short_answer: bool = False
    answer_mode: str = "balanced"  # strict | balanced | citizen
    reference_date: str | None = None  # ISO date for temporal filtering (YYYY-MM-DD)
    deep_analysis: bool = False  # Use 4-stage multi-stage reasoning pipeline
    doc_types: list[str] | None = None  # Filter: zakon, zakonik, pravilnik, uredba, odluka, sudska_praksa, bilten, strucni_tekst, ostalo
    min_authority: int | None = None  # Filter: 1=Ustav, 2=Zakon, 3=Uredba, 4=Pravilnik, 5=Ostalo


class ChatCreate(BaseModel):
    title: str = "Nova konverzacija"


class DocumentUpload(BaseModel):
    title: str
    content: str
    category: str = ""


class SupportReport(BaseModel):
    type: str
    description: str
    include_last_chat: bool = False


class ExternalAIRequest(BaseModel):
    provider: str  # openai, anthropic, google, xai, perplexity, deepseek, mistral, groq
    api_key: str
    prompt: str
    model: str | None = None
    anonymize: bool = True
    names_to_hide: list[str] = []  # Extra names/entities to anonymize


class TemplateCreate(BaseModel):
    name: str
    doc_type: str = "ostalo"
    example_text: str


class SmartFillRequest(BaseModel):
    description: str


class ValidateRequest(BaseModel):
    field_values: dict


class DraftCreate(BaseModel):
    template_id: int
    name: str = "Novi nacrt"
    field_values: dict = {}


class DraftUpdate(BaseModel):
    name: str | None = None
    field_values: dict | None = None
    status: str | None = None


class VerifyConversationRequest(BaseModel):
    chat_id: int
    provider: str  # openai, anthropic, google, xai, perplexity, deepseek, mistral, groq
    api_key: str
    model: str | None = None
    anonymize: bool = True
    names_to_hide: list[str] = []


# ── Drafting Studio Enhancement schemas ─────────────────────────────────────

class AnalyzeRiskRequest(BaseModel):
    text: str
    doc_type: str = "ugovor"


class CheckCompletenessRequest(BaseModel):
    text: str
    doc_type: str = "ugovor"


class LegalBasisRequest(BaseModel):
    text: str


class ExplainClauseRequest(BaseModel):
    text: str
    action: str = "explain"  # explain | simplify | formalize
