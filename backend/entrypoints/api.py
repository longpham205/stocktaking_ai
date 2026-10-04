"""`uvicorn entrypoints.api:app --workers 1`: one process, because the model lives in its memory/GPU."""

from app.main import create_app

app = create_app()
