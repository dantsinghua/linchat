"""UtteranceAggregator — 多轮话语聚合器

014-jarvis-ambient-voice: 在 ambient 模式下缓冲多段 ASR 转录，
静默超时后聚合为完整文本，触发 on_aggregated 回调。
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Coroutine, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class AggregatedMessage:
    """聚合后的完整消息。"""

    text: str
    utterance_count: int
    first_ts: float
    last_ts: float


class AggregatorState(str, Enum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    AGGREGATED = "AGGREGATED"


class UtteranceAggregator:
    """话语聚合器 — 缓冲 ASR 转录，静默超时后合并输出。

    Args:
        on_aggregated: 聚合完成回调（接收 AggregatedMessage）。
        timeout: 静默超时秒数，默认 VOICE_AMBIENT_AGGREGATE_TIMEOUT。
        max_buffer_size: 最大缓冲话语数，默认 VOICE_AMBIENT_MAX_BUFFER_SIZE。
    """

    def __init__(
        self,
        on_aggregated: Callable[[AggregatedMessage], Coroutine],
        timeout: Optional[float] = None,
        max_buffer_size: Optional[int] = None,
    ) -> None:
        self._on_aggregated = on_aggregated
        self._timeout = timeout or settings.VOICE_AMBIENT_AGGREGATE_TIMEOUT
        self._max_buffer_size = max_buffer_size or settings.VOICE_AMBIENT_MAX_BUFFER_SIZE

        self._utterances: list[str] = []
        self._timestamps: list[float] = []
        self._timer_task: Optional[asyncio.Task] = None
        self._state = AggregatorState.IDLE

    @property
    def state(self) -> AggregatorState:
        return self._state

    @property
    def buffer_count(self) -> int:
        return len(self._utterances)

    @property
    def timeout_remaining(self) -> float:
        """当前 timer 剩余秒数（无 timer 时返回 0）。"""
        if not self._timer_task or self._timer_task.done():
            return 0.0
        return self._timeout

    async def add(self, text: str) -> None:
        """追加一段转录文本到缓冲区。

        - 重置超时计时器
        - 达到 max_buffer_size 时自动 flush
        """
        text = text.strip()
        if not text:
            return

        self._utterances.append(text)
        self._timestamps.append(time.monotonic())
        self._state = AggregatorState.COLLECTING

        logger.debug(
            "Aggregator add: count=%d, text=%s",
            len(self._utterances),
            text[:30],
        )

        # 达到上限自动 flush
        if len(self._utterances) >= self._max_buffer_size:
            logger.info(
                "Aggregator max buffer reached (%d), auto flush",
                self._max_buffer_size,
            )
            await self._do_aggregate()
            return

        # 重置 timer
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._on_timeout())

    async def flush(self) -> None:
        """立即触发聚合（停止词触发时调用）。"""
        self._cancel_timer()
        if self._utterances:
            await self._do_aggregate()

    def reset(self) -> None:
        """清空缓冲区，不触发回调。"""
        self._cancel_timer()
        self._utterances.clear()
        self._timestamps.clear()
        self._state = AggregatorState.IDLE
        logger.debug("Aggregator reset")

    def destroy(self) -> None:
        """销毁聚合器，取消所有异步任务。"""
        self._cancel_timer()
        self._utterances.clear()
        self._timestamps.clear()
        self._state = AggregatorState.IDLE

    async def _on_timeout(self) -> None:
        """Timer 回调 — 超时后聚合缓冲区。"""
        try:
            await asyncio.sleep(self._timeout)
            if self._utterances:
                await self._do_aggregate()
        except asyncio.CancelledError:
            pass

    async def _do_aggregate(self) -> None:
        """执行聚合并调用回调。"""
        if not self._utterances:
            return

        aggregated = AggregatedMessage(
            text=" ".join(self._utterances),
            utterance_count=len(self._utterances),
            first_ts=self._timestamps[0],
            last_ts=self._timestamps[-1],
        )

        # 清空缓冲区
        self._utterances.clear()
        self._timestamps.clear()
        self._state = AggregatorState.AGGREGATED

        logger.info(
            "Aggregator completed: count=%d, text=%s",
            aggregated.utterance_count,
            aggregated.text[:50],
        )

        try:
            await self._on_aggregated(aggregated)
        except Exception:
            logger.exception("Aggregator on_aggregated callback error")
        finally:
            self._state = AggregatorState.IDLE

    def _cancel_timer(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None
