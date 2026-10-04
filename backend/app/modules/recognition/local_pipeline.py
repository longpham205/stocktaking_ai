"""The real recognizer: the engine's pipeline, loaded once in this process.

Importing this module imports the engine and, through it, faiss and (for the real backends) torch.
Only `app.main.build_recognizer` imports it, and only when RECOGNIZER=local.
"""

from pathlib import Path

from engine.core.config import build_config
from engine.inference.infer import InferenceRunner


class LocalRecognizer:
    name = "local"

    def __init__(self, pipeline_config: Path, catalog_db_url: str):
        if not pipeline_config.is_file():
            raise FileNotFoundError(f"PIPELINE_CONFIG không tồn tại: {pipeline_config}")
        # the engine loads its weights here, once; a failure stops the process at startup
        # the catalog is the web's database, whatever catalog source the YAML names for the CLI
        self._runner = InferenceRunner(build_config(pipeline_config, catalog_db_url=catalog_db_url))

    def close(self) -> None:
        return None
