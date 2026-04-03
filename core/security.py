"""JWT authentication."""
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.hash import bcrypt
from core.config import settings

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


def create_token(username: str, role: str = "user") -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "role": role, "exp": expire},
                     settings.secret_key, algorithm=ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.verify(plain, hashed)
