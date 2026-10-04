"""The recognition queue: the API process holds the model, so captures are recognised one at a time
on a single thread (one GPU, 4 GB), in the order they arrived.

The worker only owns the queue and the thread. What a job does (read the capture, write the order
lines) is the handler it is started with: `CapturesService.process`.

Limit, as in the legacy web: a recognition that runs past the timeout is reported as an error, but
the thread cannot be killed; it finishes in the background and the next job waits behind it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

from app.core.errors import Unavailable
from app.modules.recognition.ports import Recognition, RecognizerPort

logger = logging.getLogger("app.recognition.worker")

Handler = Callable[[int], Awaitable[None]]


class RecognitionWorker:
    def __init__(self, recognizer: RecognizerPort, queue_max: int, timeout_seconds: float):
        self.recognizer, self.timeout_seconds = recognizer, timeout_seconds
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=queue_max)
        # capture ids waiting, oldest first; and the one being processed
        self._waiting: list[int] = []
        self._current: int | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recognizer")
        self._task: asyncio.Task[None] | None = None

    def start(self, handler: Handler) -> None:
        """Start consuming. Inside the running event loop (the app's lifespan)."""
        self._task = asyncio.create_task(self._consume(handler), name="recognition-worker")

    @property
    def full(self) -> bool:
        return self._queue.full()

    def submit(self, capture_id: int) -> None:
        try:
            self._queue.put_nowait(capture_id)
        except asyncio.QueueFull:
            raise Unavailable("Hệ thống đang bận, thử lại sau ít giây", code="QUEUE_FULL") from None
        self._waiting.append(capture_id)

    def position(self, capture_id: int) -> int:
        """How many captures are ahead of this one in the queue (0 once it is being processed)."""
        return self._waiting.index(capture_id) if capture_id in self._waiting else 0

    def knows(self, capture_id: int) -> bool:
        """Queued or being processed by this process. A capture the database says is pending but
        the worker does not know was cut off by a restart."""
        return capture_id == self._current or capture_id in self._waiting

    async def recognize(
        self, image_path: str, similarity_threshold: float | None, min_confidence_accept: float | None
    ) -> Recognition:
        """Run the recognizer on the worker thread. TimeoutError past `timeout_seconds`."""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor, self.recognizer.recognize, image_path, similarity_threshold, min_confidence_accept
        )
        return await asyncio.wait_for(future, self.timeout_seconds)

    async def _consume(self, handler: Handler) -> None:
        while True:
            capture_id = await self._queue.get()
            self._waiting.remove(capture_id)
            self._current = capture_id
            try:
                await handler(capture_id)
            except Exception:  # one broken job must not stop the queue
                logger.exception("recognition job failed", extra={"capture_id": capture_id})
            finally:
                self._current = None
                self._queue.task_done()

    async def close(self) -> None:
        """Stop taking jobs. A job cut off here stays pending in the database and is reported as
        interrupted by a restart when asked for."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
