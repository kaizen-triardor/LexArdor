"""Initialize database with default admin user and legal schema."""
from passlib.hash import bcrypt
from db.models import get_db, init_db, get_user, create_user
from db.legal_schema import init_legal_schema
from core.config import settings


def setup_database():
    init_db()
    init_legal_schema()
    if not get_user(settings.default_admin_user):
        pw_hash = bcrypt.hash(settings.default_admin_pass)
        create_user(settings.default_admin_user, pw_hash, role="admin")
        print(f"Created default admin user: {settings.default_admin_user}")
    else:
        print("Database already initialized")
