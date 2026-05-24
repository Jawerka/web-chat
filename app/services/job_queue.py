"""
Очередь тяжёлых синхронных операций (SD HTTP, extract PDF) — P1.2.

Ограничивает параллелизм отдельным пулом потоков, чтобы не забивать
default executor и не блокировать event loop при нескольких вкладках.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class JobCancelled(Exception):
    """Операция отменена через cancel_event до или после выполнения."""


class ShutdownInProgress(Exception):
    """Сервер завершает работу — новые heavy jobs не принимаются."""


@dataclass
class _Job:
    fn: Callable[..., T]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    cancel_event: asyncio.Event | None
    future: asyncio.Future[T]


class HeavyJobQueue:
    """asyncio.Queue + выделенный ThreadPoolExecutor."""

    def __init__(self, workers: int) -> None:
        self._workers = max(1, workers)
        self._queue: asyncio.Queue[_Job[Any] | None] = asyncio.Queue()
        self._executor = ThreadPoolExecutor(
            max_workers=self._workers,
            thread_name_prefix="heavy-job",
        )
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._shutting_down = False

    def begin_shutdown(self) -> None:
        """Запретить постановку новых задач (graceful shutdown)."""
        self._shutting_down = True

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self._workers):
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(index)))
        logger.info("HeavyJobQueue: %d worker(s)", self._workers)

    async def stop(self, *, drain_timeout: float | None = None) -> None:
        if not self._started:
            return
        self._shutting_down = True
        timeout = (
            settings.shutdown_drain_sec
            if drain_timeout is None
            else drain_timeout
        )

        if timeout > 0 and self._queue.qsize() > 0:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "HeavyJobQueue: drain timeout (≈%d в очереди)",
                    self._queue.qsize(),
                )
                self._fail_queued_jobs()

        for _ in self._worker_tasks:
            await self._queue.put(None)
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._started = False
        self._shutting_down = False
        logger.info("HeavyJobQueue: остановлена")

    def _fail_queued_jobs(self) -> None:
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if job is None:
                self._queue.task_done()
                continue
            if not job.future.done():
                job.future.set_exception(JobCancelled())
            self._queue.task_done()

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    async def _worker_loop(self, worker_id: int) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    break
                if job.cancel_event is not None and job.cancel_event.is_set():
                    job.future.set_exception(JobCancelled())
                    continue
                try:
                    result = await loop.run_in_executor(
                        self._executor,
                        lambda j=job: j.fn(*j.args, **j.kwargs),
                    )
                except Exception as exc:
                    if not job.future.done():
                        job.future.set_exception(exc)
                    continue
                if job.cancel_event is not None and job.cancel_event.is_set():
                    job.future.set_exception(JobCancelled())
                elif not job.future.done():
                    job.future.set_result(result)
            finally:
                self._queue.task_done()
        logger.debug("HeavyJobQueue worker %d exit", worker_id)

    @property
    def pending_count(self) -> int:
        """Число задач в очереди (ожидают worker)."""
        return self._queue.qsize()

    async def run_sync(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        cancel_event: asyncio.Event | None = None,
        operation: str = "heavy",
        **kwargs: Any,
    ) -> T:
        """Поставить синхронную функцию в очередь и дождаться результата."""
        if self._shutting_down:
            raise ShutdownInProgress()
        await self._ensure_started()
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelled()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        await self._queue.put(
            _Job(
                fn=fn,
                args=args,
                kwargs=kwargs,
                cancel_event=cancel_event,
                future=future,
            ),
        )
        logger.debug("HeavyJobQueue enqueue: %s (depth≈%d)", operation, self._queue.qsize())
        try:
            return await future
        except JobCancelled:
            logger.info("HeavyJobQueue cancelled: %s", operation)
            raise


heavy_job_queue = HeavyJobQueue(settings.job_queue_workers)
