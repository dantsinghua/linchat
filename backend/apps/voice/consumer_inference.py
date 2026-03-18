"""InferenceMixin — Pipeline 启动、空闲超时

010-voice-agent-pipeline: 删除 enriched/STT 轮询/Gateway 推理编排，
转为通过 VoicePipeline 触发 Agent + TTS。
"""

import asyncio
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


class InferenceMixin:

    async def _start_voice_pipeline(
        self, segment_id: str, text: str, speaker_id: str | None = None,
        pipeline_user_id: int | None = None,
    ) -> None:
        """启动 VoicePipeline — 由 EventMixin._on_transcription_completed 调用。

        根据 session mode 选择 voice_chat 或 ambient 路径。
        Pipeline 在后台 task 中运行，不阻塞 Consumer 消息循环。
        pipeline_user_id: 消息归属用户（diarize 识别出的说话人），None 时使用连接所有者。
        """
        from apps.voice.services.voice_pipeline import VoicePipeline  # noqa: F811

        mode = getattr(self, "_mode", "ambient")
        target_uid = pipeline_user_id or self.user_id
        logger.info(
            "Pipeline launch: user=%s, target=%s, seg=%s, mode=%s, text=%s",
            self.user_id,
            target_uid,
            segment_id,
            mode,
            text[:30],
        )

        async def _wrapped():
            try:
                await self._run_pipeline_task(segment_id, text, mode, speaker_id,
                                              pipeline_user_id=target_uid)
            finally:
                if mode == "ambient":
                    await self._on_pipeline_done()

        self._pipeline_task = asyncio.create_task(_wrapped())

    async def _run_pipeline_task(
        self, segment_id: str, text: str, mode: str, speaker_id: str | None = None,
        pipeline_user_id: int | None = None,
    ) -> None:
        """Pipeline 后台任务包装 — 捕获未预期异常。"""
        target_uid = pipeline_user_id or self.user_id
        try:
            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=target_uid,
                text=text,
                segment_id=segment_id,
                consumer=self,
                mode=mode,
                speaker_id=speaker_id,
                connection_user_id=self.user_id if target_uid != self.user_id else None,
            )
        except Exception as e:
            logger.error(
                "Pipeline task error: user=%s, seg=%s, err=%s",
                self.user_id,
                segment_id,
                e,
                exc_info=True,
            )
            await self._send_json({
                "type": "error",
                "data": {
                    "code": "PIPELINE_ERROR",
                    "message": "语音推理管道异常",
                    "recoverable": True,
                },
            })

    def _is_pipeline_busy(self) -> bool:
        task = getattr(self, "_pipeline_task", None)
        return task is not None and not task.done()

    async def _on_pipeline_done(self) -> None:
        pending = getattr(self, "_pending_text", None)
        if not pending:
            return
        pending_speaker = getattr(self, "_pending_speaker_user_id", None)
        self._pending_text = None
        self._pending_speaker_user_id = None

        is_speaking = getattr(self, "_is_speaking", False)

        # 选择正确的 aggregator（per-speaker 或 legacy）
        speaker_aggs = getattr(self, "_speaker_aggregators", {})
        if pending_speaker and pending_speaker in speaker_aggs:
            aggregator = speaker_aggs[pending_speaker]
        else:
            aggregator = getattr(self, "_aggregator", None)

        if is_speaking or (aggregator and aggregator.state == "COLLECTING"):
            if aggregator:
                await aggregator.add(pending)
                logger.info(
                    "Pipeline done, fed pending to aggregator: user=%s, speaker=%s, speaking=%s",
                    self.user_id,
                    pending_speaker,
                    is_speaking,
                )
        else:
            logger.info(
                "Pipeline done, processing pending: user=%s, speaker=%s, text='%s'",
                self.user_id,
                pending_speaker,
                pending[:80],
            )
            await self._start_voice_pipeline(
                getattr(self, "_current_segment_id", None) or "pending",
                pending,
                pipeline_user_id=pending_speaker,
            )

    def _reset_response_state(self) -> None:
        self._current_response_id = self._response_start_time = None
        self._accumulated_content, self._response_cancelled = "", False

    async def _idle_timeout_loop(self) -> None:
        try:
            # ambient 模式不启用空闲超时
            if getattr(self, "_mode", None) == "ambient":
                return
            while True:
                await asyncio.sleep(15)
                elapsed = time.time() - self._last_activity
                if elapsed >= settings.VOICE_IDLE_TIMEOUT:
                    logger.info(
                        "Idle timeout: user=%s, idle=%ds",
                        self.user_id,
                        int(elapsed),
                    )
                    await self._send_json({
                        "type": "session.closed",
                        "data": {
                            "status": "idle_timeout",
                            "reason": "连接空闲超时",
                        },
                    })
                    await self.close(code=4003)
                    return
        except asyncio.CancelledError:
            pass
