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
        self, segment_id: str, text: str, speaker_id: str | None = None
    ) -> None:
        """启动 VoicePipeline — 由 EventMixin._on_transcription_completed 调用。

        根据 session mode 选择 voice_chat 或 continuous_listen 路径。
        Pipeline 在后台 task 中运行，不阻塞 Consumer 消息循环。
        """
        from apps.voice.services.voice_pipeline import VoicePipeline  # noqa: F811

        mode = getattr(self, "_session_mode", "voice_chat")
        logger.info(
            "Pipeline launch: user=%s, seg=%s, mode=%s, text=%s",
            self.user_id,
            segment_id,
            mode,
            text[:30],
        )

        # 在后台任务中运行 pipeline，不阻塞 WS 消息循环
        asyncio.create_task(
            self._run_pipeline_task(segment_id, text, mode, speaker_id)
        )

    async def _run_pipeline_task(
        self, segment_id: str, text: str, mode: str, speaker_id: str | None = None
    ) -> None:
        """Pipeline 后台任务包装 — 捕获未预期异常。"""
        try:
            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=self.user_id,
                text=text,
                segment_id=segment_id,
                consumer=self,
                mode=mode,
                speaker_id=speaker_id,
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

    def _reset_response_state(self) -> None:
        self._current_response_id = self._response_start_time = None
        self._accumulated_content, self._response_cancelled = "", False

    async def _idle_timeout_loop(self) -> None:
        try:
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
