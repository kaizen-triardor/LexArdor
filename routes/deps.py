"""Shared dependencies for route handlers."""
from fastapi import Request, HTTPException
from core.config import settings
from db.models import get_user


def get_current_user(request: Request = None) -> dict:
    user = get_user(settings.default_admin_user)
    if not user:
        raise HTTPException(status_code=500, detail="Default user not found")
    return user
