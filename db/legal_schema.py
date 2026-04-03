"""Legal corpus structured metadata schema — SQLite tables for canonical legal data.

This module provides the structured data layer for LexArdor's Legal Expert Engine.
ChromaDB stores vectors for retrieval; SQLite stores structured legal metadata
(document types, authority levels, temporal validity, cross-references, sub-article structure).
"""
import json
import sqlite3
from pathlib import Path
from core.config import settings


def get_legal_db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_legal_schema():
    """Create legal metadata tables. Safe to call multiple times."""
    conn = get_legal_db()
    conn.executescript("""
        -- The law/regulation as a whole
        CREATE TABLE IF NOT EXISTS legal_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            doc_type TEXT NOT NULL DEFAULT 'zakon',
            authority_level INTEGER DEFAULT 3,
            gazette_ref TEXT,
            gazette_numbers TEXT,
            latest_gazette TEXT,
            valid_from TEXT,
            valid_to TEXT,
            source_url TEXT,
            scraped_at TEXT,
            article_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- Individual articles with sub-structure counts
        CREATE TABLE IF NOT EXISTS legal_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            article_number TEXT NOT NULL,
            article_number_sort INTEGER DEFAULT 0,
            chapter TEXT DEFAULT '',
            chapter_number TEXT DEFAULT '',
            full_text TEXT NOT NULL,
            stav_count INTEGER DEFAULT 0,
            tacka_count INTEGER DEFAULT 0,
            chroma_id TEXT,
            FOREIGN KEY (document_id) REFERENCES legal_documents(id) ON DELETE CASCADE,
            UNIQUE(document_id, article_number)
        );

        -- Sub-article structure (stavovi and tačke)
        CREATE TABLE IF NOT EXISTS legal_sub_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            stav_number INTEGER,
            tacka_number INTEGER,
            text TEXT NOT NULL,
            FOREIGN KEY (article_id) REFERENCES legal_articles(id) ON DELETE CASCADE
        );

        -- Cross-references between articles
        CREATE TABLE IF NOT EXISTS citation_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_article_id INTEGER NOT NULL,
            target_document_slug TEXT,
            target_article_number TEXT,
            target_stav INTEGER,
            target_tacka INTEGER,
            citation_text TEXT,
            ref_type TEXT DEFAULT 'internal',
            FOREIGN KEY (source_article_id) REFERENCES legal_articles(id) ON DELETE CASCADE
        );

        -- Amendment/version history derived from gazette refs
        CREATE TABLE IF NOT EXISTS legal_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            gazette_number TEXT NOT NULL,
            gazette_year INTEGER,
            gazette_issue INTEGER,
            change_type TEXT DEFAULT 'amendment',
            change_note TEXT DEFAULT '',
            FOREIGN KEY (document_id) REFERENCES legal_documents(id) ON DELETE CASCADE
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_legal_articles_doc ON legal_articles(document_id);
        CREATE INDEX IF NOT EXISTS idx_legal_articles_number ON legal_articles(article_number);
        CREATE INDEX IF NOT EXISTS idx_legal_sub_articles_art ON legal_sub_articles(article_id);
        CREATE INDEX IF NOT EXISTS idx_citation_edges_source ON citation_edges(source_article_id);
        CREATE INDEX IF NOT EXISTS idx_citation_edges_target ON citation_edges(target_document_slug, target_article_number);
        CREATE INDEX IF NOT EXISTS idx_legal_versions_doc ON legal_versions(document_id);
        CREATE INDEX IF NOT EXISTS idx_legal_documents_type ON legal_documents(doc_type);
        CREATE INDEX IF NOT EXISTS idx_legal_documents_authority ON legal_documents(authority_level);
    """)
    conn.commit()
    conn.close()
    print("Legal schema initialized")


# ── Document CRUD ────────────────────────────────────────────────────────────

def upsert_legal_document(
    slug: str,
    title: str,
    doc_type: str = "zakon",
    authority_level: int = 3,
    gazette_ref: str = "",
    gazette_numbers: list[str] | None = None,
    latest_gazette: str = "",
    valid_from: str | None = None,
    valid_to: str | None = None,
    source_url: str = "",
    scraped_at: str = "",
    article_count: int = 0,
) -> int:
    """Insert or update a legal document. Returns document ID."""
    conn = get_legal_db()
    gazette_nums_json = json.dumps(gazette_numbers or [])
    conn.execute("""
        INSERT INTO legal_documents (slug, title, doc_type, authority_level, gazette_ref,
            gazette_numbers, latest_gazette, valid_from, valid_to, source_url,
            scraped_at, article_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(slug) DO UPDATE SET
            title=excluded.title, doc_type=excluded.doc_type,
            authority_level=excluded.authority_level, gazette_ref=excluded.gazette_ref,
            gazette_numbers=excluded.gazette_numbers, latest_gazette=excluded.latest_gazette,
            valid_from=excluded.valid_from, valid_to=excluded.valid_to,
            source_url=excluded.source_url, scraped_at=excluded.scraped_at,
            article_count=excluded.article_count, updated_at=CURRENT_TIMESTAMP
    """, (slug, title, doc_type, authority_level, gazette_ref, gazette_nums_json,
          latest_gazette, valid_from, valid_to, source_url, scraped_at, article_count))
    doc_id = conn.execute("SELECT id FROM legal_documents WHERE slug=?", (slug,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return doc_id


def upsert_legal_article(
    document_id: int,
    article_number: str,
    full_text: str,
    chapter: str = "",
    chapter_number: str = "",
    stav_count: int = 0,
    tacka_count: int = 0,
    chroma_id: str = "",
) -> int:
    """Insert or update an article. Returns article ID."""
    # Parse numeric sort key from article number (e.g. "24a" -> 24)
    sort_num = 0
    num_part = ""
    for ch in article_number:
        if ch.isdigit():
            num_part += ch
        else:
            break
    if num_part:
        sort_num = int(num_part)

    conn = get_legal_db()
    conn.execute("""
        INSERT INTO legal_articles (document_id, article_number, article_number_sort,
            chapter, chapter_number, full_text, stav_count, tacka_count, chroma_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id, article_number) DO UPDATE SET
            full_text=excluded.full_text, chapter=excluded.chapter,
            chapter_number=excluded.chapter_number, stav_count=excluded.stav_count,
            tacka_count=excluded.tacka_count, chroma_id=excluded.chroma_id
    """, (document_id, article_number, sort_num, chapter, chapter_number,
          full_text, stav_count, tacka_count, chroma_id))
    art_id = conn.execute(
        "SELECT id FROM legal_articles WHERE document_id=? AND article_number=?",
        (document_id, article_number)
    ).fetchone()["id"]
    conn.commit()
    conn.close()
    return art_id


def insert_sub_articles(article_id: int, sub_articles: list[dict]):
    """Bulk insert sub-articles (stavovi/tačke) for an article. Clears existing first."""
    conn = get_legal_db()
    conn.execute("DELETE FROM legal_sub_articles WHERE article_id=?", (article_id,))
    for sa in sub_articles:
        conn.execute("""
            INSERT INTO legal_sub_articles (article_id, stav_number, tacka_number, text)
            VALUES (?, ?, ?, ?)
        """, (article_id, sa.get("stav"), sa.get("tacka"), sa["text"]))
    conn.commit()
    conn.close()


def insert_citation_edges(article_id: int, cross_refs: list[dict]):
    """Bulk insert cross-references for an article. Clears existing first."""
    conn = get_legal_db()
    conn.execute("DELETE FROM citation_edges WHERE source_article_id=?", (article_id,))
    for ref in cross_refs:
        conn.execute("""
            INSERT INTO citation_edges (source_article_id, target_document_slug,
                target_article_number, target_stav, target_tacka, citation_text, ref_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (article_id, ref.get("target_law_slug", ""),
              ref.get("target_article", ""), ref.get("target_stav"),
              ref.get("target_tacka"), ref.get("citation_text", ""),
              ref.get("ref_type", "internal")))
    conn.commit()
    conn.close()


def insert_legal_versions(document_id: int, versions: list[dict]):
    """Bulk insert gazette versions for a document. Clears existing first."""
    conn = get_legal_db()
    conn.execute("DELETE FROM legal_versions WHERE document_id=?", (document_id,))
    for v in versions:
        conn.execute("""
            INSERT INTO legal_versions (document_id, gazette_number, gazette_year,
                gazette_issue, change_type, change_note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (document_id, v["number"], v.get("year"), v.get("issue"),
              v.get("change_type", "amendment"), v.get("note", "")))
    conn.commit()
    conn.close()


# ── Query functions ──────────────────────────────────────────────────────────

def get_document_by_slug(slug: str) -> dict | None:
    conn = get_legal_db()
    row = conn.execute("SELECT * FROM legal_documents WHERE slug=?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_document_articles(slug: str) -> list[dict]:
    conn = get_legal_db()
    rows = conn.execute("""
        SELECT a.* FROM legal_articles a
        JOIN legal_documents d ON a.document_id = d.id
        WHERE d.slug = ?
        ORDER BY a.article_number_sort, a.article_number
    """, (slug,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_article_detail(slug: str, article_number: str) -> dict | None:
    """Get article with sub-articles and cross-references."""
    conn = get_legal_db()
    art = conn.execute("""
        SELECT a.* FROM legal_articles a
        JOIN legal_documents d ON a.document_id = d.id
        WHERE d.slug = ? AND a.article_number = ?
    """, (slug, article_number)).fetchone()
    if not art:
        conn.close()
        return None
    result = dict(art)
    result["sub_articles"] = [dict(r) for r in conn.execute(
        "SELECT * FROM legal_sub_articles WHERE article_id=? ORDER BY stav_number, tacka_number",
        (art["id"],)
    ).fetchall()]
    result["cross_references"] = [dict(r) for r in conn.execute(
        "SELECT * FROM citation_edges WHERE source_article_id=?",
        (art["id"],)
    ).fetchall()]
    conn.close()
    return result


def get_cross_references_for_article(article_id: int) -> list[dict]:
    conn = get_legal_db()
    rows = conn.execute(
        "SELECT * FROM citation_edges WHERE source_article_id=?", (article_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_inbound_references(slug: str, article_number: str) -> list[dict]:
    """Find all articles that reference this article."""
    conn = get_legal_db()
    rows = conn.execute("""
        SELECT ce.*, a.article_number as source_article, d.slug as source_slug, d.title as source_title
        FROM citation_edges ce
        JOIN legal_articles a ON ce.source_article_id = a.id
        JOIN legal_documents d ON a.document_id = d.id
        WHERE ce.target_document_slug = ? AND ce.target_article_number = ?
    """, (slug, article_number)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_document_versions(slug: str) -> list[dict]:
    conn = get_legal_db()
    rows = conn.execute("""
        SELECT v.* FROM legal_versions v
        JOIN legal_documents d ON v.document_id = d.id
        WHERE d.slug = ?
        ORDER BY v.gazette_year, v.gazette_issue
    """, (slug,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_corpus_summary() -> dict:
    """Get structured corpus statistics."""
    conn = get_legal_db()
    total_docs = conn.execute("SELECT COUNT(*) as c FROM legal_documents").fetchone()["c"]
    total_articles = conn.execute("SELECT COUNT(*) as c FROM legal_articles").fetchone()["c"]
    total_sub = conn.execute("SELECT COUNT(*) as c FROM legal_sub_articles").fetchone()["c"]
    total_refs = conn.execute("SELECT COUNT(*) as c FROM citation_edges").fetchone()["c"]
    total_versions = conn.execute("SELECT COUNT(*) as c FROM legal_versions").fetchone()["c"]

    by_type = conn.execute("""
        SELECT doc_type, COUNT(*) as count, SUM(article_count) as articles
        FROM legal_documents GROUP BY doc_type ORDER BY count DESC
    """).fetchall()

    by_authority = conn.execute("""
        SELECT authority_level, COUNT(*) as count
        FROM legal_documents GROUP BY authority_level ORDER BY authority_level
    """).fetchall()

    conn.close()
    return {
        "total_documents": total_docs,
        "total_articles": total_articles,
        "total_sub_articles": total_sub,
        "total_cross_references": total_refs,
        "total_versions": total_versions,
        "by_type": [dict(r) for r in by_type],
        "by_authority": [dict(r) for r in by_authority],
    }
