"""Support report endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends

from core.config import settings
from db.models import get_user_chats, get_chat_messages
from routes.schemas import SupportReport
from routes.deps import get_current_user

router = APIRouter(prefix="/api", tags=["support"])


@router.post("/support/report")
def submit_report(req: SupportReport, user: dict = Depends(get_current_user)):
    """Save support report locally and attempt to email it."""
    # Map type to Serbian subject label
    type_labels = {
        "bug": "Bug report",
        "suggestion": "Predlog",
        "question": "Pitanje",
    }
    type_label = type_labels.get(req.type, req.type)

    # Build email body
    body_parts = [
        f"Instalacija: #{settings.installation_id}",
        f"Licenca: {settings.license_firm}",
        f"Verzija: {settings.app_version}",
        f"Korisnik: {user['username']}",
        f"Datum: {datetime.utcnow().strftime('%d.%m.%Y %H:%M')}",
        "",
        f"Vrsta: {req.type}",
        "",
        f"Opis:\n{req.description}",
    ]

    if req.include_last_chat:
        # Get last chat's last 2 messages (user + assistant)
        chats = get_user_chats(user["id"])
        if chats:
            msgs = get_chat_messages(chats[0]["id"])
            if msgs:
                body_parts.append("\n--- Poslednja konverzacija ---")
                for m in msgs[-2:]:
                    role = "Pitanje" if m["role"] == "user" else "Odgovor"
                    body_parts.append(f"{role}: {m['content'][:500]}")
                    if m.get("confidence"):
                        body_parts.append(f"Pouzdanost: {m['confidence']}")

    body = "\n".join(body_parts)
    subject = f"[LexArdor] {type_label}: {req.description[:60]}"

    # Save locally
    from db.models import get_db
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, type TEXT, description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO support_reports (user_id, type, description) VALUES (?, ?, ?)",
                 (user["id"], req.type, req.description))
    conn.commit()
    conn.close()

    # Attempt to send email via SMTP
    emailed = False
    if settings.smtp_host and settings.smtp_user and settings.smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = settings.smtp_user
            msg["To"] = "triardor.studio@gmail.com"

            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_pass)
                server.send_message(msg)
            emailed = True
        except Exception:
            pass  # SMTP not configured or failed — report is saved locally

    message = "Prijava sačuvana i poslata" if emailed else "Prijava sačuvana"

    return {
        "ok": True,
        "emailed": emailed,
        "message": message,
        "mailto_to": settings.support_email,
        "mailto_subject": subject,
        "mailto_body": body,
    }


@router.get("/support/reports")
def list_reports(user: dict = Depends(get_current_user)):
    from db.models import get_db
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, type TEXT, description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    rows = conn.execute(
        "SELECT id, type, description, created_at FROM support_reports WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
