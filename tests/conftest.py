"""Use fixed test configuration before importing the app; no private .env needed."""
import os

os.environ.update({
    "JWT_SECRET_KEY": "test-only-signing-secret-at-least-32-characters",
    "FRONTEND_URL": "http://127.0.0.1:5173/src/dashboard/index.html",
    "MONGODB_URI": "mongodb://localhost",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_REDIRECT_URI": "http://127.0.0.1:8000/auth/google/callback",
})
