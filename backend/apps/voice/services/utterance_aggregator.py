import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Coroutine, Optional

from django.conf import settings

from apps.common.async_utils import cancel_task_sync

logger = logging.getLogger(__name__)


@dataclass
class AggregatedMessage:
    text: str
    utterance_count: int
    first_ts: float
    last_ts: float


class UtteranceAggregator:
    def __init__(self, on_aggregated: Callable[[AggregatedMessage], Coroutine],
                 timeout: Optional[float] = None, max_buffer_size: Optional[int] = None,
                 adaptive_flush: Optional[bool] = None,
                 sentence_end_chars: Optional[str] = None) -> None:
        self._on_aggregated = on_aggregated
        self._timeout = timeout or settings.VOICE_AMBIENT_AGGREGATE_TIMEOUT
        self._max_buffer_size = max_buffer_size or settings.VOICE_AMBIENT_MAX_BUFFER_SIZE
        self._adaptive_flush = (settings.VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED
                                if adaptive_flush is None else adaptive_flush)
        self._sentence_end_chars = (sentence_end_chars
                                    or settings.VOICE_AMBIENT_SENTENCE_END_CHARS)
        self._utterances: list[str] = []
        self._timestamps: list[float] = []
        self._timer_task: Optional[asyncio.Task] = None
        self._state = "IDLE"
        self._last_flush_reason = "manual"

    @property
    def state(self) -> str:
        return self._state

    @property
    def buffer_count(self) -> int:
        return len(self._utterances)

    @property
    def timeout_remaining(self) -> float:
        if not self._timer_task or self._timer_task.done():
            return 0.0
        return self._timeout

    async def add(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._utterances.append(text)
        self._timestamps.append(time.monotonic())
        self._state = "COLLECTING"
        if len(self._utterances) >= self._max_buffer_size:
            self._last_flush_reason = "max_buffer"
            await self._do_aggregate()
            return
        if self._adaptive_flush and self._is_utterance_complete(text):
            cancel_task_sync(self._timer_task)
            self._timer_task = None
            self._last_flush_reason = "sentence_end"
            await self._do_aggregate()
            return
        cancel_task_sync(self._timer_task)
        self._timer_task = asyncio.create_task(self._on_timeout())

    def _is_utterance_complete(self, text: str) -> bool:
        """高置信「话说完」判定：仅看本条 text 结尾字符是否句末标点/疑问结束符。

        逗号/顿号/无标点 → False（断句中途，等超时兜底）。
        """
        if not text:
            return False
        return text[-1] in self._sentence_end_chars

    async def flush(self) -> None:
        cancel_task_sync(self._timer_task)
        if self._utterances:
            self._last_flush_reason = "manual"
            await self._do_aggregate()

    def reset(self) -> None:
        cancel_task_sync(self._timer_task)
        self._timer_task = None
        self._utterances.clear()
        self._timestamps.clear()
        self._state = "IDLE"

    def destroy(self) -> None:
        self.reset()

    async def _on_timeout(self) -> None:
        try:
            await asyncio.sleep(self._timeout)
            if self._utterances:
                self._last_flush_reason = "timeout"
                await self._do_aggregate()
        except asyncio.CancelledError:
            pass

    async def _do_aggregate(self) -> None:
        if not self._utterances:
            return
        aggregated = AggregatedMessage(
            text=" ".join(self._utterances), utterance_count=len(self._utterances),
            first_ts=self._timestamps[0], last_ts=self._timestamps[-1])
        # batch-07 跳5：聚合器静默等待打点（03 分析 4 号瓶颈，固定 ~1.5s 等待需量化）
        # 无 user_id/segment_id 上下文，只记 wait/span 供人工核对；不写入 latency tracker。
        logger.info("voice", extra={"stage": "ambient.aggregation.flush",
                    "utterance_count": aggregated.utterance_count,
                    "flush_reason": self._last_flush_reason,
                    "wait_ms": int((time.monotonic() - self._timestamps[-1]) * 1000),
                    "span_ms": int((self._timestamps[-1] - self._timestamps[0]) * 1000)})
        self._utterances.clear()
        self._timestamps.clear()
        self._state = "AGGREGATED"
        try:
            await self._on_aggregated(aggregated)
        except Exception:
            logger.exception("Aggregator callback error")
        finally:
            self._state = "IDLE"
