"""voice Consumer 共享状态契约（typing.Protocol，仅类型检查期使用，零运行时行为）。

3-Mixin 架构（SessionMixin / EventMixin / InferenceMixin）通过 self._* 隐式共享状态。
本 Protocol 声明这些共享属性与跨 Mixin 方法的类型，使各 Mixin 可显式依赖统一接口。
类型来源全部取自现有代码实际赋值，未新增任何字段/概念。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from apps.voice.services.asr_stream_client import ASRStreamClient
    from apps.voice.services.utterance_aggregator import UtteranceAggregator


class VoiceConsumerProtocol(Protocol):
    # --- 身份 / 连接 ---
    user_id: int
    username: str
    _is_device_connection: bool
    # --- 基类（AsyncWebsocketConsumer）暴露面 ---
    channel_name: str
    channel_layer: Any
    scope: dict[str, Any]
    # --- ASR / 会话状态 ---
    _asr_client: Optional[ASRStreamClient]
    _configured: bool
    _mode: str
    _closed: bool
    _reconnect_lock: Optional[asyncio.Lock]
    # --- 分段 / VAD ---
    _current_segment_id: Optional[str]
    _segment_timer_task: Optional[asyncio.Task]
    _idle_check_task: Optional[asyncio.Task]
    _is_speaking: bool
    _vad_start_ts: Optional[float]
    _last_activity: float
    # --- 响应状态 ---
    _current_response_id: Optional[str]
    _response_start_time: Optional[float]
    _response_cancelled: bool
    _accumulated_content: str
    _pipeline_task: Optional[asyncio.Task]
    _trace_id: str
    # --- ambient 聚合 / 说话人 ---
    _aggregator: Optional[UtteranceAggregator]
    _speaker_aggregators: dict[int, Any]
    _pending_text: Optional[str]
    _pending_speaker_user_id: Optional[int]
    _last_unknown_label: Optional[str]

    # --- 跨 Mixin / 基类方法（trivial body，仅签名）---
    async def _send_json(self, data: dict[str, Any]) -> None: ...
    async def _send_binary(self, data: bytes) -> None: ...
    async def _send_error(
        self, code: str, message: str, recoverable: bool = True
    ) -> None: ...
    async def _handle_asr_event(self, event: dict[str, Any]) -> None: ...
    def _is_pipeline_busy(self) -> bool: ...
    async def _start_voice_pipeline(
        self,
        segment_id: str,
        text: str,
        speaker_id: str | None = None,
        pipeline_user_id: int | None = None,
    ) -> None: ...
    async def _idle_timeout_loop(self) -> None: ...
    def _reset_response_state(self) -> None: ...
    async def close(self, code: int | None = None) -> None: ...
    async def send(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
        close: bool = False,
    ) -> None: ...
    async def accept(self, subprotocol: str | None = None) -> None: ...
