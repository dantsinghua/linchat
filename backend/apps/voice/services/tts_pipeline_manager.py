"""TTS 播报队列管理器 (013-tts-comfort-queue)

协调安慰语音计时器、段间静默、完整文本 TTS 播放。
每次 VoicePipeline._run_pipeline_inner() 创建一个实例。

队列项类型:
  comfort  — 3s 计时器到期自动入队
  response — Agent 完整回复
  error    — Agent 推理出错
  sentinel — shutdown() 停止信号
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from django.conf import settings

from apps.voice.services.tts_stream_client import TTSStreamClient

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """TTS 播报队列项。"""

    text: str
    item_type: Literal["comfort", "response", "error", "sentinel"]


class TTSPipelineManager:
    """TTS 播报队列管理器。

    管理安慰语音计时器、段间静默、完整文本播放。
    由 VoicePipeline 在每次 pipeline 执行时创建。

    Args:
        on_audio: 音频帧回调（转发给前端 WebSocket）。
        voice: TTS 音色名称。
    """

    def __init__(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        voice: str,
    ) -> None:
        self._on_audio = on_audio
        self._voice = voice

        # 队列与 worker
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        # 安慰计时器
        self._comfort_task: asyncio.Task | None = None
        self._comfort_index: int = 0
        self._comfort_enabled: bool = True

        # 取消与空闲
        self._cancelled: bool = False
        self._idle: asyncio.Event = asyncio.Event()
        self._idle.set()  # 初始空闲

        # 段间静默
        self._last_end: float = 0.0

        # 当前播放中的 TTS 客户端（供 cancel 断开）
        self._current_tts: TTSStreamClient | None = None

    # ---- 公共 API ----

    def start(self) -> None:
        """启动 worker task 和首个安慰计时器。"""
        self._worker_task = asyncio.create_task(self._worker())
        self.start_comfort_timer()

    def enqueue(self, text: str, item_type: str = "response") -> None:
        """非阻塞入队。"""
        self._idle.clear()
        self._queue.put_nowait(QueueItem(text=text, item_type=item_type))  # type: ignore[arg-type]

    def start_comfort_timer(self) -> None:
        """启动/重启安慰倒计时。"""
        # 取消已有计时器
        if self._comfort_task and not self._comfort_task.done():
            self._comfort_task.cancel()
        comfort_texts = settings.VOICE_TTS_COMFORT_TEXTS
        if self._comfort_enabled and self._comfort_index < len(comfort_texts):
            self._comfort_task = asyncio.create_task(self._comfort_countdown())

    def stop_comfort_timer(self) -> None:
        """永久停止安慰（Agent 完成/出错时调用）。"""
        self._comfort_enabled = False
        if self._comfort_task and not self._comfort_task.done():
            self._comfort_task.cancel()
        self._drain_comfort_from_queue()

    async def wait_idle(self) -> None:
        """等待所有 TTS 播完。"""
        await self._idle.wait()

    async def cancel(self) -> None:
        """Barge-in — 清空队列 + 断开 TTS + 停止 worker。"""
        self._cancelled = True
        self._comfort_enabled = False

        # 取消安慰计时器
        if self._comfort_task and not self._comfort_task.done():
            self._comfort_task.cancel()

        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        # 断开当前 TTS
        if self._current_tts:
            try:
                await self._current_tts.disconnect()
            except Exception:
                pass

        # 取消 worker task（中断 _ensure_gap sleep）
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass

        self._idle.set()

    async def shutdown(self) -> None:
        """Pipeline 结束时优雅清理。"""
        # 入队 sentinel 让 worker 退出
        self._queue.put_nowait(QueueItem(text="", item_type="sentinel"))

        # 等待 worker 完成（5s 超时）
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except (asyncio.CancelledError, Exception):
                    pass

        # 取消安慰计时器
        if self._comfort_task and not self._comfort_task.done():
            self._comfort_task.cancel()

    # ---- 内部方法 ----

    async def _worker(self) -> None:
        """Worker 主循环 — 串行处理队列项。"""
        try:
            while True:
                item = await self._queue.get()

                if self._cancelled or item.item_type == "sentinel":
                    self._queue.task_done()
                    break

                # 段间静默
                await self._ensure_gap()

                # TTS 播放
                await self._play_text(item.text)
                self._last_end = time.monotonic()

                # comfort 播完后重启计时器
                if item.item_type == "comfort" and self._comfort_enabled:
                    self.start_comfort_timer()

                self._queue.task_done()

                # 队列空 → 标记空闲
                if self._queue.empty():
                    self._idle.set()
        except asyncio.CancelledError:
            return

    async def _play_text(self, text: str) -> None:
        """连接 TTS WS 并播放文本。"""
        tts = TTSStreamClient(on_audio=self._on_audio)
        self._current_tts = tts
        try:
            await tts.connect()
            await tts.configure(voice=self._voice)
            await tts.send_text_delta(text)
            await tts.send_text_done()
            await tts.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
        except Exception:
            logger.warning("TTS play failed, skipping segment: text=%s", text[:30])
        finally:
            self._current_tts = None
            try:
                await tts.disconnect()
            except Exception:
                pass

    async def _ensure_gap(self) -> None:
        """确保段间静默间隔。"""
        if self._last_end <= 0:
            return
        gap = settings.VOICE_TTS_SEGMENT_GAP
        elapsed = time.monotonic() - self._last_end
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)

    async def _comfort_countdown(self) -> None:
        """安慰倒计时协程。"""
        try:
            await asyncio.sleep(settings.VOICE_TTS_COMFORT_DELAY)
            # 双重检查
            if not self._comfort_enabled:
                return
            comfort_texts = settings.VOICE_TTS_COMFORT_TEXTS
            if self._comfort_index < len(comfort_texts):
                self.enqueue(comfort_texts[self._comfort_index], "comfort")
                self._comfort_index += 1
        except asyncio.CancelledError:
            return

    def _drain_comfort_from_queue(self) -> None:
        """清除队列中待播的 comfort 项，保留 response/error/sentinel。"""
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
        # 如果 drain 后队列空且没有正在播放的项，标记空闲
        # （实际空闲由 worker 循环末尾判断，这里不需要 set）
