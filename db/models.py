"""SQLite database for users, chat history."""
import sqlite3
import json
from pathlib import Path
from core.config import settings


def get_db() -> sqlite3.Connection:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT DEFAULT 'Nova konverzacija',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT,
            confidence TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        );
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            doc_type TEXT DEFAULT 'ostalo',
            body_template TEXT NOT NULL,
            fields TEXT NOT NULL DEFAULT '[]',
            example_values TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            template_id INTEGER NOT NULL,
            name TEXT DEFAULT 'Novi nacrt',
            field_values TEXT DEFAULT '{}',
            status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (template_id) REFERENCES templates(id)
        );
        CREATE TABLE IF NOT EXISTS matters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS matter_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matter_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (matter_id) REFERENCES matters(id)
        );
        CREATE TABLE IF NOT EXISTS matter_chats (
            matter_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            PRIMARY KEY (matter_id, chat_id),
            FOREIGN KEY (matter_id) REFERENCES matters(id),
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        );
        CREATE TABLE IF NOT EXISTS matter_documents (
            matter_id INTEGER NOT NULL,
            doc_id TEXT NOT NULL,
            PRIMARY KEY (matter_id, doc_id),
            FOREIGN KEY (matter_id) REFERENCES matters(id)
        );
        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            answer_mode TEXT,
            confidence TEXT,
            source_count INTEGER DEFAULT 0,
            citation_verified INTEGER DEFAULT 0,
            citation_flagged INTEGER DEFAULT 0,
            model_used TEXT,
            bm25_used BOOLEAN DEFAULT 0,
            response_time_ms INTEGER,
            multi_stage BOOLEAN DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


# ── Query logging (observability) ──

def log_query(query: str, answer_mode: str, confidence: str, source_count: int,
              citation_verified: int, citation_flagged: int, model_used: str,
              bm25_used: bool, response_time_ms: int, multi_stage: bool = False):
    """Log a query and its diagnostics to the query_logs table."""
    conn = get_db()
    conn.execute(
        """INSERT INTO query_logs
           (query, answer_mode, confidence, source_count, citation_verified,
            citation_flagged, model_used, bm25_used, response_time_ms, multi_stage)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (query, answer_mode, confidence, source_count, citation_verified,
         citation_flagged, model_used, int(bm25_used), response_time_ms, int(multi_stage)))
    conn.commit()
    conn.close()


def get_query_logs(limit: int = 50) -> list[dict]:
    """Retrieve recent query logs."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM query_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_query_diagnostics() -> dict:
    """Aggregate diagnostics from query_logs."""
    conn = get_db()

    # Total queries
    total = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
    if total == 0:
        conn.close()
        return {
            "total_queries": 0,
            "avg_response_time_ms": 0,
            "confidence_distribution": {},
            "citation_accuracy": {"verified": 0, "flagged": 0, "ratio": 0},
            "model_usage": {},
            "answer_mode_distribution": {},
            "multi_stage_count": 0,
            "bm25_usage_count": 0,
        }

    # Average response time
    avg_time = conn.execute(
        "SELECT AVG(response_time_ms) FROM query_logs"
    ).fetchone()[0] or 0

    # Confidence distribution
    confidence_rows = conn.execute(
        "SELECT confidence, COUNT(*) as cnt FROM query_logs GROUP BY confidence"
    ).fetchall()
    confidence_dist = {r["confidence"] or "unknown": r["cnt"] for r in confidence_rows}

    # Citation accuracy
    citation_stats = conn.execute(
        "SELECT SUM(citation_verified) as verified, SUM(citation_flagged) as flagged FROM query_logs"
    ).fetchone()
    verified = citation_stats["verified"] or 0
    flagged = citation_stats["flagged"] or 0
    citation_total = verified + flagged
    citation_ratio = round(verified / citation_total, 3) if citation_total > 0 else 0

    # Model usage
    model_rows = conn.execute(
        "SELECT model_used, COUNT(*) as cnt FROM query_logs GROUP BY model_used"
    ).fetchall()
    model_usage = {r["model_used"] or "unknown": r["cnt"] for r in model_rows}

    # Answer mode distribution
    mode_rows = conn.execute(
        "SELECT answer_mode, COUNT(*) as cnt FROM query_logs GROUP BY answer_mode"
    ).fetchall()
    mode_dist = {r["answer_mode"] or "unknown": r["cnt"] for r in mode_rows}

    # Multi-stage and BM25 counts
    multi_stage = conn.execute(
        "SELECT COUNT(*) FROM query_logs WHERE multi_stage = 1"
    ).fetchone()[0]
    bm25_count = conn.execute(
        "SELECT COUNT(*) FROM query_logs WHERE bm25_used = 1"
    ).fetchone()[0]

    conn.close()

    return {
        "total_queries": total,
        "avg_response_time_ms": round(avg_time),
        "confidence_distribution": confidence_dist,
        "citation_accuracy": {
            "verified": verified,
            "flagged": flagged,
            "ratio": citation_ratio,
        },
        "model_usage": model_usage,
        "answer_mode_distribution": mode_dist,
        "multi_stage_count": multi_stage,
        "bm25_usage_count": bm25_count,
    }


# ── User operations ──
def get_user(username: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(username: str, password_hash: str, role: str = "user") -> int:
    conn = get_db()
    cur = conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                       (username, password_hash, role))
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


# ── Chat operations ──
def create_chat(user_id: int, title: str = "Nova konverzacija") -> int:
    conn = get_db()
    cur = conn.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (user_id, title))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def get_user_chats(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chats WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_message(chat_id: int, role: str, content: str,
                sources: list = None, confidence=None) -> int:
    # confidence can be string or dict — serialize dicts to JSON string
    if isinstance(confidence, dict):
        conf_str = json.dumps(confidence)
    else:
        conf_str = confidence
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO messages (chat_id, role, content, sources, confidence) VALUES (?, ?, ?, ?, ?)",
        (chat_id, role, content, json.dumps(sources) if sources else None, conf_str),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def get_chat_messages(chat_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,)
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        if d["sources"]:
            d["sources"] = json.loads(d["sources"])
        results.append(d)
    return results


def update_chat_title(chat_id: int, title: str):
    conn = get_db()
    conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
    conn.commit()
    conn.close()


def delete_chat(chat_id: int):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()


# ── Template operations ──

def _parse_template_row(row) -> dict:
    """Convert a template row to dict, JSON-parsing stored fields."""
    d = dict(row)
    for col in ("fields", "example_values"):
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def create_template(user_id: int, name: str, doc_type: str, body_template: str,
                    fields: list, example_values: dict = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO templates (user_id, name, doc_type, body_template, fields, example_values) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, doc_type, body_template,
         json.dumps(fields, ensure_ascii=False),
         json.dumps(example_values or {}, ensure_ascii=False)),
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_template(template_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    return _parse_template_row(row) if row else None


def get_user_templates(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM templates WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [_parse_template_row(r) for r in rows]


def update_template(template_id: int, **kwargs):
    allowed = {"name", "doc_type", "body_template", "fields", "example_values"}
    sets, vals = [], []
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k in ("fields", "example_values"):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(template_id)
    conn = get_db()
    conn.execute(f"UPDATE templates SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_template(template_id: int):
    conn = get_db()
    conn.execute("DELETE FROM drafts WHERE template_id = ?", (template_id,))
    conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()


# ── Draft operations ──

def _parse_draft_row(row) -> dict:
    """Convert a draft row to dict, JSON-parsing stored fields."""
    d = dict(row)
    if d.get("field_values"):
        try:
            d["field_values"] = json.loads(d["field_values"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def create_draft(user_id: int, template_id: int, name: str = "Novi nacrt",
                 field_values: dict = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO drafts (user_id, template_id, name, field_values) VALUES (?, ?, ?, ?)",
        (user_id, template_id, name,
         json.dumps(field_values or {}, ensure_ascii=False)),
    )
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return did


def get_draft(draft_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    conn.close()
    return _parse_draft_row(row) if row else None


def get_user_drafts(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM drafts WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [_parse_draft_row(r) for r in rows]


def update_draft(draft_id: int, **kwargs):
    allowed = {"name", "field_values", "status"}
    sets, vals = [], []
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "field_values":
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(draft_id)
    conn = get_db()
    conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_draft(draft_id: int):
    conn = get_db()
    conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    conn.commit()
    conn.close()


# ── Matters (Research Workspace) ─────────────────────────────────────────────

def create_matter(user_id: int, name: str, description: str = "") -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO matters (user_id, name, description) VALUES (?, ?, ?)",
        (user_id, name, description))
    matter_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return matter_id


def get_user_matters(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM matters WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)).fetchall()
    matters = []
    for r in rows:
        m = dict(r)
        # Count linked items
        m["chat_count"] = conn.execute(
            "SELECT COUNT(*) FROM matter_chats WHERE matter_id=?", (m["id"],)).fetchone()[0]
        m["doc_count"] = conn.execute(
            "SELECT COUNT(*) FROM matter_documents WHERE matter_id=?", (m["id"],)).fetchone()[0]
        m["note_count"] = conn.execute(
            "SELECT COUNT(*) FROM matter_notes WHERE matter_id=?", (m["id"],)).fetchone()[0]
        matters.append(m)
    conn.close()
    return matters


def get_matter(matter_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM matters WHERE id = ?", (matter_id,)).fetchone()
    if not row:
        conn.close()
        return None
    m = dict(row)
    m["notes"] = [dict(r) for r in conn.execute(
        "SELECT * FROM matter_notes WHERE matter_id=? ORDER BY created_at DESC",
        (matter_id,)).fetchall()]
    m["chat_ids"] = [r[0] for r in conn.execute(
        "SELECT chat_id FROM matter_chats WHERE matter_id=?",
        (matter_id,)).fetchall()]
    m["doc_ids"] = [r[0] for r in conn.execute(
        "SELECT doc_id FROM matter_documents WHERE matter_id=?",
        (matter_id,)).fetchall()]
    conn.close()
    return m


def update_matter(matter_id: int, **kwargs):
    sets, vals = [], []
    for k, v in kwargs.items():
        if v is not None and k in ("name", "description", "status"):
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(matter_id)
    conn = get_db()
    conn.execute(f"UPDATE matters SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_matter(matter_id: int):
    conn = get_db()
    conn.execute("DELETE FROM matter_notes WHERE matter_id = ?", (matter_id,))
    conn.execute("DELETE FROM matter_chats WHERE matter_id = ?", (matter_id,))
    conn.execute("DELETE FROM matter_documents WHERE matter_id = ?", (matter_id,))
    conn.execute("DELETE FROM matters WHERE id = ?", (matter_id,))
    conn.commit()
    conn.close()


def add_matter_note(matter_id: int, content: str) -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO matter_notes (matter_id, content) VALUES (?, ?)",
        (matter_id, content))
    note_id = cursor.lastrowid
    conn.execute("UPDATE matters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (matter_id,))
    conn.commit()
    conn.close()
    return note_id


def delete_matter_note(note_id: int):
    conn = get_db()
    conn.execute("DELETE FROM matter_notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()


def link_chat_to_matter(matter_id: int, chat_id: int):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO matter_chats (matter_id, chat_id) VALUES (?, ?)",
                 (matter_id, chat_id))
    conn.execute("UPDATE matters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (matter_id,))
    conn.commit()
    conn.close()


def link_doc_to_matter(matter_id: int, doc_id: str):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO matter_documents (matter_id, doc_id) VALUES (?, ?)",
                 (matter_id, doc_id))
    conn.execute("UPDATE matters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (matter_id,))
    conn.commit()
    conn.close()


# ── Query Logs (Observability) ───────────────────────────────────────────────

def _ensure_query_logs_table():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS query_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        answer_mode TEXT,
        confidence TEXT,
        source_count INTEGER DEFAULT 0,
        citation_verified INTEGER DEFAULT 0,
        citation_flagged INTEGER DEFAULT 0,
        model_used TEXT,
        bm25_used BOOLEAN DEFAULT 0,
        response_time_ms INTEGER,
        multi_stage BOOLEAN DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()


def log_query(query: str, answer_mode: str = "", confidence: str = "",
              source_count: int = 0, citation_verified: int = 0,
              citation_flagged: int = 0, model_used: str = "",
              bm25_used: bool = False, response_time_ms: int = 0,
              multi_stage: bool = False):
    _ensure_query_logs_table()
    conn = get_db()
    conn.execute(
        """INSERT INTO query_logs
           (query, answer_mode, confidence, source_count, citation_verified,
            citation_flagged, model_used, bm25_used, response_time_ms, multi_stage)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (query, answer_mode, confidence, source_count, citation_verified,
         citation_flagged, model_used, bm25_used, response_time_ms, multi_stage))
    conn.commit()
    conn.close()


def get_query_diagnostics(limit: int = 50) -> dict:
    _ensure_query_logs_table()
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
    if total == 0:
        conn.close()
        return {"total_queries": 0, "avg_response_time_ms": 0,
                "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
                "citation_accuracy": {"verified_pct": 0, "flagged_pct": 0},
                "bm25_usage_pct": 0, "recent": []}

    avg_time = conn.execute("SELECT AVG(response_time_ms) FROM query_logs").fetchone()[0] or 0
    conf = {}
    for row in conn.execute("SELECT confidence, COUNT(*) as c FROM query_logs GROUP BY confidence"):
        conf[row["confidence"] or "unknown"] = round(row["c"] / total * 100)
    total_cites = conn.execute("SELECT SUM(citation_verified), SUM(citation_flagged) FROM query_logs").fetchone()
    v, f = total_cites[0] or 0, total_cites[1] or 0
    cite_total = v + f
    bm25_count = conn.execute("SELECT COUNT(*) FROM query_logs WHERE bm25_used=1").fetchone()[0]
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM query_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {
        "total_queries": total,
        "avg_response_time_ms": round(avg_time),
        "confidence_distribution": conf,
        "citation_accuracy": {
            "verified_pct": round(v / cite_total * 100) if cite_total else 0,
            "flagged_pct": round(f / cite_total * 100) if cite_total else 0,
        },
        "bm25_usage_pct": round(bm25_count / total * 100) if total else 0,
        "recent": recent[:20],
    }
