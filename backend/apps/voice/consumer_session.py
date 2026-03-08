"""SessionMixin — 会话管理、ASR 连接、关闭、取消、音频帧

010-voice-agent-pipeline: GatewayClient 替换为 ASRStreamClient。
"""

import asyncio
import logging
from typing import Any, Optional

from django.conf import settings

from apps.voice.services.asr_stream_client import ASRStreamClient
from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)


class SessionMixin:

    async def _connect_asr(self) -> bool:
        """创建 ASRStreamClient 并建立连接。"""
        if self._asr_client and self._asr_client.connected:
            await self._asr_client.disconnect()
        self._asr_client = ASRStreamClient(on_event=self._handle_asr_event)
        try:
            await self._asr_client.connect()
            return True
        except Exception as e:
            logger.warning("ASR connect failed: user=%s, err=%s", self.user_id, e)
            return False

    def _start_idle_check(self) -> None:
        if self._idle_check_task and not self._idle_check_task.done():
            self._idle_check_task.cancel()
        self._idle_check_task = asyncio.create_task(self._idle_timeout_loop())

    async def _connect_and_configure_asr(self) -> Optional[str]:
        """连接并配置 ASR。成功返回 None，失败返回错误原因 ('connect'/'configure')。"""
        if not await self._connect_asr():
            return "connect"
        try:
            await self._asr_client.configure()
            return None
        except Exception as e:
            logger.warning("ASR configure failed: user=%s, err=%s", self.user_id, e)
            await self._asr_client.disconnect()
            return "configure"

    def _normalize_mode(self, data: dict[str, Any]) -> str:
        """标准化语音模式 — voice_chat_enriched 静默映射为 voice_chat (SC-008)。"""
        mode = data.get("mode", "voice_chat")
        if mode == "voice_chat_enriched":
            logger.warning(
                "Deprecated mode 'voice_chat_enriched' → 'voice_chat': user=%s",
                self.user_id,
            )
            mode = "voice_chat"
        if mode not in ("voice_chat", "ambient"):
            mode = "voice_chat"
        return mode

    async def _handle_session_configure(self, data: dict[str, Any]) -> None:
        mode = self._normalize_mode(data)
        created = await voice_session_service.create_session(self.user_id, mode=mode)
        if not created:
            await self._send_json({
                "type": "session.conflict",
                "data": {"message": "检测到其他标签页的活跃语音会话，已自动接管"},
            })
            await voice_session_service.close_session(self.user_id)
            await voice_session_service.create_session(self.user_id, mode=mode)

        asr_err = await self._connect_and_configure_asr()
        if asr_err:
            code = "GATEWAY_CONFIGURE_FAILED" if asr_err == "configure" else "GATEWAY_CONNECT_FAILED"
            msg = "语音服务配置失败" if asr_err == "configure" else "语音服务连接失败，请稍后重试"
            await self._send_error(code, msg, recoverable=False)
            await voice_session_service.close_session(self.user_id)
            return

        self._mode = mode
        await voice_session_service.update_session(
            self.user_id,
            upstream_connected=True,
            asr_session_id=self._asr_client.session_id,
        )
        self._configured = True
        self._start_idle_check()

        # ambient 模式：初始化话语聚合器
        if self._mode == "ambient":
            from apps.voice.services.utterance_aggregator import UtteranceAggregator

            self._aggregator = UtteranceAggregator(
                on_aggregated=self._on_utterance_aggregated,
            )

        configured_data: dict[str, Any] = {
            "status": "active",
            "session_id": self._asr_client.session_id,
            "mode": self._mode,
        }
        if self._mode == "ambient":
            configured_data["features"] = {
                "utterance_aggregation": True,
                "llm_decision": settings.VOICE_DECISION_USE_LLM,
                "cross_device_tts": True,
            }
        await self._send_json({
            "type": "session.configured",
            "data": configured_data,
        })
        logger.info(
            "Voice configured: user=%s, mode=%s, asr=%s",
            self.user_id,
            self._mode,
            self._asr_client.session_id,
        )

    async def _on_utterance_aggregated(self, aggregated_msg) -> None:
        """聚合器回调 — 聚合完成后触发决策 + Pipeline。"""
        from apps.voice.services.response_decision_service import response_decision_service

        logger.info(
            "Aggregated: user=%s, count=%d, text=%s",
            self.user_id,
            aggregated_msg.utterance_count,
            aggregated_msg.text[:50],
        )

        # 发送 aggregation.completed 事件
        await self._send_json({
            "type": "aggregation.completed",
            "data": {
                "aggregated_text": aggregated_msg.text,
                "utterance_count": aggregated_msg.utterance_count,
                "first_ts": aggregated_msg.first_ts,
                "last_ts": aggregated_msg.last_ts,
            },
        })

        # 决策
        decision, reason = await response_decision_service.decide(
            aggregated_msg.text,
            speaker_id=None,
            user_id=self.user_id,
            mode="ambient",
        )

        # 发送 decision.result 事件
        await self._send_json({
            "type": "decision.result",
            "data": {"decision": decision.value, "reason": reason},
        })

        # 执行决策
        if decision.value == "RESPOND":
            segment_id = self._current_segment_id or "agg"
            await self._start_voice_pipeline(
                segment_id, aggregated_msg.text, speaker_id=None
            )
        elif decision.value == "RECORD_ONLY":
            # 静默保存 — 通过 VoicePipeline._record_only 路径
            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.record_only_ambient(
                user_id=self.user_id,
                text=aggregated_msg.text,
                consumer=self,
            )

    async def _handle_session_reconnect(self, data: dict[str, Any]) -> None:
        if not await voice_session_service.get_session(self.user_id):
            await self._send_json({
                "type": "session.reconnect_failed",
                "data": {
                    "reason": "no_session",
                    "message": "会话已过期，请重新开始语音模式",
                },
            })
            return

        asr_err = await self._connect_and_configure_asr()
        if asr_err:
            await voice_session_service.close_session(self.user_id)
            reason = "configure_failed" if asr_err == "configure" else "gateway_failed"
            msg = "语音服务配置失败，请重新开始" if asr_err == "configure" else "语音服务重连失败，请重新开始"
            await self._send_json({
                "type": "session.reconnect_failed",
                "data": {"reason": reason, "message": msg},
            })
            return

        self._mode = self._normalize_mode(data)
        self._configured = True
        self._start_idle_check()
        await self._send_json({
            "type": "session.reconnected",
            "data": {
                "status": "ok",
                "session_id": self._asr_client.session_id,
                "mode": self._mode,
            },
        })
        logger.info(
            "Voice reconnected: user=%s, mode=%s", self.user_id, self._mode
        )

    async def _handle_session_close(self) -> None:
        logger.info("Voice session.close: user_id=%s", self.user_id)
        if self._asr_client and self._asr_client.connected:
            await self._asr_client.disconnect()
        # 取消活跃的 segment 定时器
        self._cancel_segment_timer()
        await voice_session_service.close_session(self.user_id)
        self._configured = False
        self._reset_response_state()
        await self._send_json({"type": "session.closed", "data": {"status": "ok"}})

    async def _handle_response_cancel(self, data: dict[str, Any]) -> None:
        """取消当前推理 — 通过 VoicePipeline.cancel() 中断 Agent。"""
        # 延迟导入避免循环引用
        from apps.voice.services.voice_pipeline import VoicePipeline

        response_id = data.get("response_id", self._current_response_id)
        if not response_id:
            logger.warning("Voice cancel no response_id: user_id=%s", self.user_id)
            return
        await VoicePipeline.cancel(self.user_id)
        self._response_cancelled = True
        logger.info(
            "Voice cancelled: user=%s, response=%s", self.user_id, response_id
        )

    async def _handle_audio_frame(self, pcm_data: bytes) -> None:
        if not self._configured or not self._asr_client or not self._asr_client.connected:
            return
        await self._asr_client.send_audio(pcm_data)
        if self._current_segment_id:
            await voice_session_service.cache_audio_chunk(
                self.user_id, self._current_segment_id, pcm_data
            )
        await voice_session_service.refresh_session(self.user_id)

    # ---- ASR 重连逻辑 (ambient 模式) ----

    async def _reconnect_asr(self) -> None:
        """ambient 模式下 ASR 断连自动重连（最多 3 次）。"""
        if getattr(self, "_mode", None) != "ambient":
            return

        for attempt in range(1, 4):
            logger.info(
                "ASR reconnect attempt %d/3: user=%s", attempt, self.user_id
            )
            await asyncio.sleep(2)

            asr_err = await self._connect_and_configure_asr()
            if not asr_err:
                await voice_session_service.update_session(
                    self.user_id,
                    upstream_connected=True,
                    asr_session_id=self._asr_client.session_id,
                )
                logger.info(
                    "ASR reconnected: user=%s, asr=%s",
                    self.user_id,
                    self._asr_client.session_id,
                )
                return

        logger.error("ASR reconnect failed after 3 attempts: user=%s", self.user_id)
        await self._send_error(
            "ASR_RECONNECT_FAILED",
            "语音服务重连失败，请重新连接",
            recoverable=False,
        )

    # ---- 最大语音段时长保护 ----

    def _start_segment_timer(self) -> None:
        """启动语音段超时定时器（vad.speech_start 时调用）。"""
        self._cancel_segment_timer()
        self._segment_timer_task: Optional[asyncio.Task] = asyncio.create_task(
            self._segment_timeout()
        )

    def _cancel_segment_timer(self) -> None:
        """取消语音段超时定时器。"""
        task = getattr(self, "_segment_timer_task", None)
        if task and not task.done():
            task.cancel()
        self._segment_timer_task = None

    async def _segment_timeout(self) -> None:
        """超时后强制触发 ASR commit。"""
        try:
            await asyncio.sleep(settings.VOICE_MAX_SEGMENT_DURATION)
            if self._asr_client and self._asr_client.connected:
                await self._asr_client.send_commit()
                logger.info(
                    "Segment timeout commit: user=%s, seg=%s",
                    self.user_id,
                    self._current_segment_id,
                )
        except asyncio.CancelledError:
            pass
