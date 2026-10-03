"""The plug the web app sees the ML engine through. The app depends on this protocol only; which
adapter implements it (`local_pipeline`: the engine, `fake`: canned results) is decided once, in
`app.main.build_recognizer`. The recognition methods themselves arrive with the worker (phase 4)."""

from typing import Protocol


class RecognizerPort(Protocol):
    name: str

    def close(self) -> None: ...
