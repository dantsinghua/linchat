import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from django.conf import settings

from apps.common.async_utils import cancel_task, cancel_task_sync
from apps.voice.services.tts_stream_client import TTSStreamClient
from apps.voice.services.voice_latency import latency_record

logger = logging.getLogger(__name__)

# batch-09：流式会话结束哨兵，feed 队列收到即触发 send_text_done + wait_for_done。
_STREAM_DONE = object()


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
        # batch-07：延迟归因上下文，由 VoicePipeline._setup_tts 构造后注入
        self._user_id: int | None = None
        self._segment_id: str | None = None
        # batch-09：单条常驻流式会话（与 comfort/error 队列并存）
        self._stream_tts: TTSStreamClient | None = None
        self._stream_queue: asyncio.Queue[object] | None = None
        self._stream_task: asyncio.Task | None = None

    def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker())
        self.start_comfort_timer()

    def enqueue(self, text: str, item_type: str = "response") -> None:
        self._idle.clear()
        self._queue.put_nowait(QueueItem(text=text, item_type=item_type))  # type: ignore[arg-type]

    # batch-09：单条常驻流式会话 —— voice_pipeline 只调 begin/feed/end/abort 四个动词，WS 细节隔离在此。
    def begin_stream(self) -> None:
        """开一条常驻 TTS 会话：connect 只付一次，音频在 LLM 仍在产 token 时即回流。"""
        self._idle.clear()
        self._stream_queue = asyncio.Queue()
        self._stream_task = asyncio.create_task(self._run_stream())

    def feed_text(self, text: str) -> None:
        if self._stream_queue is not None:
            self._stream_queue.put_nowait(text)

    def end_stream(self) -> None:
        if self._stream_queue is not None:
            self._stream_queue.put_nowait(_STREAM_DONE)

    async def abort_stream(self) -> None:
        """丢弃半截流式会话（barge-in / error 中途用）。"""
        if self._stream_tts is not None:
            try:
                await self._stream_tts.disconnect()
            except Exception:
                pass
        await cancel_task(self._stream_task)
        self._stream_tts = self._stream_queue = self._stream_task = None
        self._idle.set()

    async def _run_stream(self) -> None:
        tts = TTSStreamClient(on_audio=self._on_audio)
        self._stream_tts = tts
        # batch-10：_current_tts 延迟到首帧 delta 送出时才认领（见循环内）。
        # 预连接把 begin_stream 提前到 pipeline 起点，与 comfort 播报时间窗重叠；
        # 空转期间 _current_tts 仍归 comfort 所有，避免 barge-in 断错连接。
        # barge-in 经 cancel(_stream_task) 断预连接流，与 _current_tts 无关，语义正确。
        t0 = time.monotonic()
        connect_ms = synth_ms = None
        t_synth: float | None = None
        try:
            t_connect = time.monotonic()
            await tts.connect()
            await tts.configure(voice=self._voice)
            connect_ms = int((time.monotonic() - t_connect) * 1000)  # batch-07 跳9：TTS 连接（仅一次）
            assert self._stream_queue is not None
            while True:
                chunk = await self._stream_queue.get()
                if chunk is _STREAM_DONE:
                    self._stream_queue.task_done()
                    break
                if t_synth is None:
                    t_synth = time.monotonic()   # 首帧 delta 送出时刻
                    self._current_tts = tts      # batch-10：真正出声才认领 _current_tts
                await tts.send_text_delta(chunk)  # type: ignore[arg-type]
                self._stream_queue.task_done()
            await tts.send_text_done()
            if t_synth is None:
                t_synth = time.monotonic()
            await tts.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
            synth_ms = int((time.monotonic() - t_synth) * 1000)  # batch-07 跳10：TTS 合成
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("TTS stream play failed")
        finally:
            if self._current_tts is tts:   # batch-10：仅清本流认领的（预连接空转期不误清 comfort）
                self._current_tts = None
            self._stream_tts = None
            try:
                await tts.disconnect()
            except Exception:
                pass
            self._last_end = time.monotonic()
            logger.info("voice", extra={"stage": "tts.stream_play",
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                        "connect_ms": connect_ms, "synth_ms": synth_ms})
            # batch-07 打点复用。语义变化：增量模式 tts_synth 测「首帧 delta 送出 → audio.done」
            # 窗口（含与 LLM 推理重叠段），非旧口径「全文送完 → audio.done」；见 voice_latency.latency_flush 注释。
            if self._segment_id:
                latency_record(self._user_id, self._segment_id, "tts_connect", connect_ms)
                latency_record(self._user_id, self._segment_id, "tts_synth", synth_ms)
            self._idle.set()

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
        await cancel_task(self._stream_task)   # batch-09：断开常驻流式会话（barge-in）
        self._stream_tts = self._stream_queue = self._stream_task = None
        await cancel_task(self._worker_task)
        self._idle.set()

    async def shutdown(self) -> None:
        # batch-09：wait_idle 已保证流式会话 _idle.set()（正常收尾）；此处兜底清理未完成的 stream_task
        if self._stream_task and not self._stream_task.done():
            await cancel_task(self._stream_task)
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
                logger.info("voice", extra={"stage": "tts.dequeue",
                            "item_type": item.item_type,
                            "queue_len": self._queue.qsize(),
                            "text_len": len(item.text)})
                if self._cancelled or item.item_type == "sentinel":
                    self._queue.task_done()
                    break
                await self._ensure_gap()
                await self._play_text(item.text, item.item_type)
                self._last_end = time.monotonic()
                if item.item_type == "comfort" and self._comfort_enabled:
                    self.start_comfort_timer()
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()
        except asyncio.CancelledError:
            return

    async def _play_text(self, text: str, item_type: str = "response") -> None:
        tts = TTSStreamClient(on_audio=self._on_audio)
        self._current_tts = tts
        t0 = time.monotonic()
        connect_ms = synth_ms = None
        try:
            t_connect = time.monotonic()
            await tts.connect()
            await tts.configure(voice=self._voice)
            connect_ms = int((time.monotonic() - t_connect) * 1000)  # batch-07 跳9：TTS 连接
            await tts.send_text_delta(text)
            await tts.send_text_done()
            logger.info("voice", extra={"stage": "tts.wait_done_start",
                        "text_len": len(text),
                        "timeout_s": settings.VOICE_TTS_TIMEOUT})
            t_synth = time.monotonic()
            await tts.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
            synth_ms = int((time.monotonic() - t_synth) * 1000)  # batch-07 跳10：TTS 合成
        except Exception:
            logger.warning("TTS play failed: text=%s", text[:30])
        finally:
            self._current_tts = None
            try:
                await tts.disconnect()
            except Exception:
                pass
            logger.info("voice", extra={"stage": "tts.play",
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                        "connect_ms": connect_ms, "synth_ms": synth_ms,
                        "text_len": len(text)})
            # batch-07：仅 response 类型（非 comfort/error/sentinel）计入延迟归因；
            # 汇总行由 VoicePipeline._run_inner 在 pipeline.end 统一 flush。
            if item_type == "response" and self._segment_id:
                latency_record(self._user_id, self._segment_id, "tts_connect", connect_ms)
                latency_record(self._user_id, self._segment_id, "tts_synth", synth_ms)

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
