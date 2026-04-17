import asyncio
import json
import logging
from typing import Any, Optional

from django.conf import settings

from apps.common.async_utils import cancel_task_sync
from apps.voice.services.asr_stream_client import ASRStreamClient
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis, redis_delete, redis_get, redis_setex_json

logger = logging.getLogger(__name__)

# Redis 键: 记录用户当前活跃的 ambient 设备连接
_AMBIENT_CONN_KEY = "voice:ambient_conn:{user_id}"


class SessionMixin:

    async def _connect_and_configure_asr(self) -> Optional[str]:
        if self._asr_client and self._asr_client.connected:
            await self._asr_client.disconnect()
        self._asr_client = ASRStreamClient(on_event=self._handle_asr_event)
        try:
            await self._asr_client.connect()
        except Exception as e:
            logger.warning("ASR connect failed: user=%s, err=%s", self.user_id, e)
            return "connect"
        try:
            await self._asr_client.configure()
            return None
        except Exception as e:
            logger.warning("ASR configure failed: user=%s, err=%s", self.user_id, e)
            await self._asr_client.disconnect()
            return "configure"

    def _normalize_mode(self, data: dict[str, Any]) -> str:
        mode = data.get("mode", "ambient")
        return mode if mode in ("voice_chat", "ambient") else "ambient"

    async def _handle_asr_failure(self, asr_err: str, *, close_session: bool = True) -> None:
        if asr_err == "configure":
            code, msg = "GATEWAY_CONFIGURE_FAILED", "语音服务配置失败"
        else:
            code, msg = "GATEWAY_CONNECT_FAILED", "语音服务连接失败，请稍后重试"
        await self._send_error(code, msg, recoverable=False)
        if close_session:
            await voice_session_service.close_session(self.user_id)

    async def _handle_session_configure(self, data: dict[str, Any]) -> None:
        mode = self._normalize_mode(data)
        created = await voice_session_service.create_session(self.user_id, mode=mode)
        if not created:
            await self._send_json({"type": "session.conflict",
                "data": {"message": "检测到其他标签页的活跃语音会话，已自动接管"}})
            await voice_session_service.close_session(self.user_id)
            await voice_session_service.create_session(self.user_id, mode=mode)
        asr_err = await self._connect_and_configure_asr()
        if asr_err:
            await self._handle_asr_failure(asr_err)
            return
        self._mode = mode
        await voice_session_service.update_session(
            self.user_id, upstream_connected=True, asr_session_id=self._asr_client.session_id)
        self._configured = True
        # 设备独占检查：ambient 模式下，设备连接优先于浏览器
        if self._mode == "ambient":
            rejected = await self._check_device_exclusive()
            if rejected:
                return
        self._start_idle_check()
        if self._mode == "ambient":
            from apps.voice.services.utterance_aggregator import UtteranceAggregator
            self._aggregator = UtteranceAggregator(on_aggregated=self._on_utterance_aggregated)
            self._speaker_aggregators = {}
            # [DEPRECATED] diarize 功能暂时废弃
            # from apps.voice.repositories import speaker_profile_repo
            # self._diarize_enabled = await speaker_profile_repo.any_exists()
        configured_data: dict[str, Any] = {
            "status": "active", "session_id": self._asr_client.session_id, "mode": self._mode,
            **({"features": {"utterance_aggregation": True, "llm_decision": settings.VOICE_DECISION_USE_LLM,
                "cross_device_tts": True}}
               if self._mode == "ambient" else {})}
        await self._send_json({"type": "session.configured", "data": configured_data})

    async def _check_device_exclusive(self) -> bool:
        """设备独占检查。ambient 模式下设备连接优先于浏览器。

        Returns:
            True 表示当前连接被拒绝（已发送错误并关闭 ASR），False 表示通过。
        """
        key = _AMBIENT_CONN_KEY.format(user_id=self.user_id)
        raw = await redis_get(key)
        existing = json.loads(raw) if raw else None

        if existing and existing.get("is_device") and not self._is_device_connection:
            # 已有设备连接 → 拒绝浏览器
            await self._send_error("DEVICE_EXCLUSIVE",
                "设备已在环境监听中，浏览器无法同时使用 ambient 模式", recoverable=False)
            if self._asr_client and self._asr_client.connected:
                await self._asr_client.disconnect()
            self._configured = False
            return True

        if existing:
            # 踢掉旧连接（无论旧连接是设备还是浏览器）
            old_channel = existing.get("channel_name")
            if old_channel and old_channel != self.channel_name:
                try:
                    await self.channel_layer.send(old_channel, {
                        "type": "force_disconnect",
                        "reason": "device_exclusive",
                        "message": "新的设备连接已接管 ambient 模式",
                    })
                except Exception:
                    pass  # 旧连接可能已断开

        # 注册当前连接
        await self._register_ambient_connection()
        return False

    async def _register_ambient_connection(self) -> None:
        """在 Redis 中注册当前 ambient 连接。"""
        key = _AMBIENT_CONN_KEY.format(user_id=self.user_id)
        await redis_setex_json(key, settings.VOICE_AMBIENT_SESSION_TTL, {
            "channel_name": self.channel_name,
            "is_device": self._is_device_connection,
        })

    async def _unregister_ambient_connection(self) -> None:
        """从 Redis 中注销当前 ambient 连接（仅当 key 属于本连接时删除）。"""
        key = _AMBIENT_CONN_KEY.format(user_id=self.user_id)
        raw = await redis_get(key)
        if raw:
            data = json.loads(raw)
            if data.get("channel_name") == self.channel_name:
                await redis_delete(key)

    def _get_or_create_aggregator(self, speaker_user_id: int):
        speaker_aggs = getattr(self, "_speaker_aggregators", {})
        if speaker_user_id not in speaker_aggs:
            from apps.voice.services.utterance_aggregator import UtteranceAggregator
            speaker_aggs[speaker_user_id] = UtteranceAggregator(
                on_aggregated=lambda msg, uid=speaker_user_id: self._on_utterance_aggregated(msg, speaker_user_id=uid)
            )
            self._speaker_aggregators = speaker_aggs
        return speaker_aggs[speaker_user_id]

    async def _on_utterance_aggregated(self, aggregated_msg, speaker_user_id: int = 0) -> None:
        from apps.voice.services.response_decision_service import response_decision_service
        from apps.voice.services.voice_persist_service import voice_persist_service
        target_uid = speaker_user_id or self.user_id
        is_identified = speaker_user_id > 0
        await self._send_json({"type": "aggregation.completed", "data": {
            "aggregated_text": aggregated_msg.text, "utterance_count": aggregated_msg.utterance_count,
            "first_ts": aggregated_msg.first_ts, "last_ts": aggregated_msg.last_ts,
            "speaker_user_id": target_uid}})

        # 未识别说话人 → 直接 RECORD_ONLY，不进入意图判断和回复流程
        if not is_identified:
            sid = getattr(self, "_last_unknown_label", None)
            await self._send_json({"type": "decision.result", "data": {
                "decision": "RECORD_ONLY", "reason": "unidentified_speaker", "speaker_user_id": target_uid}})
            await voice_persist_service.record_only_ambient(user_id=target_uid, text=aggregated_msg.text, speaker_id=sid)
            logger.info("Unidentified speaker record-only: user=%s, speaker=%s, text=%s",
                target_uid, sid, aggregated_msg.text[:50])
            return

        # 已识别说话人 → 走完整决策链（唤醒词/LLM 意图/规则链）
        decision, reason = await response_decision_service.decide(
            aggregated_msg.text, speaker_id=None, user_id=target_uid, mode="ambient",
            speaker_identified=is_identified)
        await self._send_json({"type": "decision.result", "data": {
            "decision": decision.value, "reason": reason, "speaker_user_id": target_uid}})
        if decision.value == "DISCARD":
            logger.info(
                "Utterance discarded (TTS echo): user=%s, reason=%s, text=%s",
                target_uid, reason, aggregated_msg.text[:50],
            )
        elif decision.value == "RESPOND":
            if self._is_pipeline_busy():
                if self._pending_text:
                    self._pending_text += " " + aggregated_msg.text
                else:
                    self._pending_text = aggregated_msg.text
                self._pending_speaker_user_id = speaker_user_id or None
                logger.info("Pipeline busy, buffered: user=%s, speaker=%s, pending='%s'",
                    self.user_id, target_uid, self._pending_text[:80])
            else:
                await self._start_voice_pipeline(
                    self._current_segment_id or "agg", aggregated_msg.text,
                    speaker_id=None, pipeline_user_id=target_uid)
        elif decision.value == "RECORD_ONLY":
            sid = str(target_uid)
            await voice_persist_service.record_only_ambient(user_id=target_uid, text=aggregated_msg.text, speaker_id=sid)

    async def _handle_session_reconnect(self, data: dict[str, Any]) -> None:
        if not await voice_session_service.get_session(self.user_id):
            await self._send_json({"type": "session.reconnect_failed",
                "data": {"reason": "no_session", "message": "会话已过期，请重新开始语音模式"}})
            return
        asr_err = await self._connect_and_configure_asr()
        if asr_err:
            await voice_session_service.close_session(self.user_id)
            reason = "configure_failed" if asr_err == "configure" else "gateway_failed"
            msg = "语音服务配置失败，请重新开始" if asr_err == "configure" else "语音服务重连失败，请重新开始"
            await self._send_json({"type": "session.reconnect_failed", "data": {"reason": reason, "message": msg}})
            return
        self._mode = self._normalize_mode(data)
        self._configured = True
        self._start_idle_check()
        await self._send_json({"type": "session.reconnected",
            "data": {"status": "ok", "session_id": self._asr_client.session_id, "mode": self._mode}})

    async def _handle_session_close(self) -> None:
        if self._asr_client and self._asr_client.connected:
            await self._asr_client.disconnect()
        cancel_task_sync(getattr(self, "_segment_timer_task", None))
        await voice_session_service.close_session(self.user_id)
        self._configured = False
        self._reset_response_state()
        await self._send_json({"type": "session.closed", "data": {"status": "ok"}})

    async def _handle_response_cancel(self, data: dict[str, Any]) -> None:
        from apps.voice.services.voice_pipeline import VoicePipeline
        await VoicePipeline.cancel(self.user_id)
        self._response_cancelled = True

    async def _handle_audio_frame(self, pcm_data: bytes) -> None:
        if not self._configured or not self._asr_client or not self._asr_client.connected:
            return
        await self._asr_client.send_audio(pcm_data)
        if self._current_segment_id:
            await voice_session_service.cache_audio_chunk(self.user_id, self._current_segment_id, pcm_data)
        await voice_session_service.refresh_session(self.user_id)

    def _start_idle_check(self) -> None:
        cancel_task_sync(getattr(self, "_idle_check_task", None))
        self._idle_check_task = asyncio.create_task(self._idle_timeout_loop())

    def _start_segment_timer(self) -> None:
        cancel_task_sync(getattr(self, "_segment_timer_task", None))
        self._segment_timer_task = asyncio.create_task(self._segment_timeout())

    async def _segment_timeout(self) -> None:
        try:
            await asyncio.sleep(settings.VOICE_MAX_SEGMENT_DURATION)
            if self._asr_client and self._asr_client.connected:
                await self._asr_client.send_commit()
        except asyncio.CancelledError:
            pass

    async def _reconnect_asr(self) -> None:
        if getattr(self, "_mode", None) != "ambient":
            return
        for attempt in range(1, 4):
            await asyncio.sleep(2)
            asr_err = await self._connect_and_configure_asr()
            if not asr_err:
                await voice_session_service.update_session(
                    self.user_id, upstream_connected=True, asr_session_id=self._asr_client.session_id)
                logger.info("ASR reconnected: user=%s", self.user_id)
                return
        logger.error("ASR reconnect failed after 3 attempts: user=%s", self.user_id)
        await self._send_error("ASR_RECONNECT_FAILED", "语音服务重连失败，请重新连接", recoverable=False)
