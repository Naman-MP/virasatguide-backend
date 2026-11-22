"""Password hashing and JWT helpers."""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Use Argon2 for new passwords and still accept older bcrypt hashes.
pwd_context = PasswordHash((Argon2Hasher(), BcryptHasher()))


def get_secret_key() -> str:
    # Read the private key after .env has been loaded.
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("Set JWT_SECRET_KEY to a random secret of at least 32 characters.")
    return secret


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # A damaged password hash should reject login, not crash the app.
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (UnknownHashError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": expires}, get_secret_key(), algorithm=ALGORITHM)


AUTH_COOKIE = "virasat_access_token"


def get_frontend_url() -> str:
    """A fixed trusted destination, never a redirect supplied by the browser."""
    value = os.getenv("FRONTEND_URL", "")
    if not value:
        return value
    parsed = urlsplit(value)
    local = parsed.hostname in {"localhost", "127.0.0.1"}
    valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and local)
    has_extra_parts = parsed.username or parsed.password or parsed.fragment
    if not parsed.netloc or has_extra_parts or not valid_scheme:
        raise RuntimeError("FRONTEND_URL must be HTTPS, or HTTP on localhost for development")
    return value


def get_frontend_origin() -> str:
    parsed = urlsplit(get_frontend_url())
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
