"""Use fixed test configuration before importing the app; no private .env needed."""
import os

os.environ.update({
    "VERCEL_ENV": "development",
    "VERCEL": "0",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "OLLAMA_API_KEY": "",
    "OLLAMA_MODEL": "test-model",
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "OLLAMA_THINK": "false",
    "JWT_SECRET_KEY": "test-only-signing-secret-at-least-32-characters",
    "FRONTEND_URL": "http://127.0.0.1:5173/src/dashboard/index.html",
    "MONGODB_URI": "mongodb://localhost",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_REDIRECT_URI": "http://127.0.0.1:8000/auth/google/callback",
})
