"""RECOGNIZER=fake must start the API without the engine's heavy dependencies.

Run in a fresh interpreter: this test process has already imported the engine (tests/conftest.py).
"""

import json
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAVY = ("torch", "faiss", "transformers", "sam2", "easyocr", "rfdetr", "cv2")

_PROBE = """
import asyncio, json, os, sys
os.environ["RECOGNIZER"] = "fake"
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "import-boundary-probe-secret-0123456789abcdef"
from app.main import create_app

async def main():
    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.backends.recognizer.name == "fake"

asyncio.run(main())
loaded = sorted({name.split(".")[0] for name in sys.modules})
print(json.dumps(loaded))
"""


def test_fake_recognizer_does_not_import_the_engine_stack() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert "app" in loaded
    assert not loaded & set(HEAVY), f"imported by the fake path: {sorted(loaded & set(HEAVY))}"
    assert "engine" not in loaded, "the fake path must not import the engine at all"
