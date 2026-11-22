"""Command-line shortcut: start the local API on port 8000."""

def main() -> None:
    """Start the FastAPI development server on the loopback interface."""
    import uvicorn

    uvicorn.run("virasatguide_backend.main:app", host="127.0.0.1", port=8000)
