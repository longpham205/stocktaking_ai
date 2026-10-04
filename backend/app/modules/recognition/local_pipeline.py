"""The real recognizer: the engine's pipeline, loaded once in this process.

Importing this module imports the engine and, through it, faiss and (for the real backends) torch.
Only `app.main.build_recognizer` imports it, and only when RECOGNIZER=local.
"""

from pathlib import Path

from engine.core.config import build_config
from engine.inference.infer import InferenceRunner


class LocalRecognizer:
    name = "local"

    def __init__(self, pipeline_config: Path):
        if not pipeline_config.is_file():
            raise FileNotFoundError(f"PIPELINE_CONFIG không tồn tại: {pipeline_config}")
        # the engine loads its weights here, once; a failure stops the process at startup
        self._runner = InferenceRunner(build_config(pipeline_config))

    def close(self) -> None:
        return None
