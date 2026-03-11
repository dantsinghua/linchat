import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from django.conf import settings

from apps.common.async_utils import cancel_task, cancel_task_sync
from apps.voice.services.tts_stream_client import TTSStreamClient

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    text: str
    item_type: Literal["comfort", "response", "error", "sentinel"]


class TTSPipelineManager:
    def __init__(self, on_audio: Callable[[bytes], Awaitable[None]], voice: str) -> None:
        self._on_audio = on_audio
        self._voice = voice
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._comfort_task: asyncio.Task | None = None
        self._comfort_index: int = 0
        self._comfort_enabled: bool = True
        self._cancelled: bool = False
        self._idle: asyncio.Event = asyncio.Event()
        self._idle.set()
        self._last_end: float = 0.0
        self._current_tts: TTSStreamClient | None = None

    def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker())
        self.start_comfort_timer()

    def enqueue(self, text: str, item_type: str = "response") -> None:
        self._idle.clear()
        self._queue.put_nowait(QueueItem(text=text, item_type=item_type))  # type: ignore[arg-type]

    def start_comfort_timer(self) -> None:
        cancel_task_sync(self._comfort_task)
        comfort_texts = settings.VOICE_TTS_COMFORT_TEXTS
        if self._comfort_enabled and self._comfort_index < len(comfort_texts):
            self._comfort_task = asyncio.create_task(self._comfort_countdown())

    def stop_comfort_timer(self) -> None:
        self._comfort_enabled = False
        cancel_task_sync(self._comfort_task)
        self._drain_comfort_from_queue()

    async def wait_idle(self) -> None:
        await self._idle.wait()

    async def cancel(self) -> None:
        self._cancelled = True
        self._comfort_enabled = False
        cancel_task_sync(self._comfort_task)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        if self._current_tts:
            try:
                await self._current_tts.disconnect()
            except Exception:
                pass
        await cancel_task(self._worker_task)
        self._idle.set()

    async def shutdown(self) -> None:
        self._queue.put_nowait(QueueItem(text="", item_type="sentinel"))
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await cancel_task(self._worker_task)
        cancel_task_sync(self._comfort_task)

    async def _worker(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if self._cancelled or item.item_type == "sentinel":
                    self._queue.task_done()
                    break
                await self._ensure_gap()
                await self._play_text(item.text)
                self._last_end = time.monotonic()
                if item.item_type == "comfort" and self._comfort_enabled:
                    self.start_comfort_timer()
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()
        except asyncio.CancelledError:
            return

    async def _play_text(self, text: str) -> None:
        tts = TTSStreamClient(on_audio=self._on_audio)
        self._current_tts = tts
        try:
            await tts.connect()
            await tts.configure(voice=self._voice)
            await tts.send_text_delta(text)
            await tts.send_text_done()
            await tts.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
        except Exception:
            logger.warning("TTS play failed: text=%s", text[:30])
        finally:
            self._current_tts = None
            try:
                await tts.disconnect()
            except Exception:
                pass

    async def _ensure_gap(self) -> None:
        if self._last_end <= 0:
            return
        remaining = settings.VOICE_TTS_SEGMENT_GAP - (time.monotonic() - self._last_end)
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _comfort_countdown(self) -> None:
        try:
            await asyncio.sleep(settings.VOICE_TTS_COMFORT_DELAY)
            if self._comfort_enabled and self._comfort_index < len(settings.VOICE_TTS_COMFORT_TEXTS):
                self.enqueue(settings.VOICE_TTS_COMFORT_TEXTS[self._comfort_index], "comfort")
                self._comfort_index += 1
        except asyncio.CancelledError:
            return

    def _drain_comfort_from_queue(self) -> None:
        kept: list[QueueItem] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item.item_type != "comfort":
                    kept.append(item)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        for item in kept:
            self._queue.put_nowait(item)
