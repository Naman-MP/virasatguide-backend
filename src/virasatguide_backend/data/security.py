"""Password hashing and JWT helpers."""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

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
    """Load the JWT signing key after environment initialization and check its length."""
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("Set JWT_SECRET_KEY to a random secret of at least 32 characters.")
    return secret


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # A damaged password hash should reject login, not crash the app.
    """Check a password against a supported hash and reject damaged hashes."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (UnknownHashError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    """Hash a plaintext password with the configured Argon2 implementation."""
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    """Sign a fresh application token with an expiry and unique token ID."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": expires, "jti": str(uuid4())}, get_secret_key(), algorithm=ALGORITHM)


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
    """Extract the origin from the configured frontend return URL."""
    parsed = urlsplit(get_frontend_url())
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


def get_trusted_origins() -> list[str]:
    """Explicit app origins; local development accepts both loopback host names."""
    origins = {"https://virasat-frontend-nu.vercel.app"}
    configured = get_frontend_origin()
    if configured:
        origins.add(configured)
    if os.getenv("VERCEL_ENV") != "production":
        origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    return sorted(origins)
