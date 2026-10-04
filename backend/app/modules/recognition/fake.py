"""A recognizer that loads no model: for development on a machine without a GPU and for the test
suite. It must stay importable without torch, faiss or the engine's pipeline modules
(tests/test_import_boundary.py)."""


class FakeRecognizer:
    name = "fake"

    def close(self) -> None:
        return None
