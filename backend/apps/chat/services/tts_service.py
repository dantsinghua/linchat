"""
TTS 语音合成服务 (T059)

将 AI 回复文本转换为语音音频，透传 Gateway /v1/audio/speech 接口。

参考:
- specs/008-multimodal-minicpm/contracts/tts.yaml
- spec.md US6 - AI 语音回复
- docs/upstream-integration-guide.md
"""

import logging
import time
from typing import Optional

import httpx
from django.conf import settings

from apps.chat.repositories import message_repo
from apps.common.gateway_utils import (
    build_gateway_headers,
    record_gateway_span,
)

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """TTS 服务错误"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        data: Optional[dict] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.data = data
        super().__init__(message)


class TTSService:
    """TTS 语音合成服务

    接收 message_uuid，查询消息内容，调用 Gateway TTS 接口返回音频流。
    """

    @staticmethod
    async def synthesize(
        user_id: int,
        message_uuid: str,
        voice: str = "default",
    ) -> bytes:
        """合成语音

        Args:
            user_id: 用户 ID
            message_uuid: AI 回复消息的 UUID
            voice: 语音风格（预留扩展）

        Returns:
            音频字节数据 (audio/mpeg)

        Raises:
            TTSError: 合成失败
        """
        # 1. 查询消息
        message = await message_repo.get_by_uuid(message_uuid, user_id)
        if not message:
            raise TTSError(
                code="MESSAGE_NOT_FOUND",
                message="消息不存在",
                status_code=404,
            )

        # 2. 校验角色
        if message.role != "assistant":
            raise TTSError(
                code="INVALID_MESSAGE",
                message="仅支持 AI 回复消息的语音合成",
                status_code=400,
            )

        # 3. 校验文本长度
        text = message.content or ""
        max_length = getattr(settings, "TTS_MAX_TEXT_LENGTH", 2000)
        if len(text) > max_length:
            raise TTSError(
                code="TEXT_TOO_LONG",
                message=f"文本长度超出限制（最大 {max_length} 字符）",
                status_code=400,
            )

        if not text.strip():
            raise TTSError(
                code="INVALID_MESSAGE",
                message="消息内容为空，无法合成语音",
                status_code=400,
            )

        # 4. 调用 Gateway TTS 接口
        gateway_url = getattr(settings, "LLM_GATEWAY_URL", "")
        if not gateway_url:
            raise TTSError(
                code="TTS_SERVICE_UNAVAILABLE",
                message="语音合成服务暂时不可用，请稍后重试",
                status_code=503,
            )

        tts_url = f"{gateway_url}/v1/audio/speech"
        headers = build_gateway_headers()
        timeout = getattr(settings, "LLM_GATEWAY_TTS_TIMEOUT", 60)
        model = getattr(settings, "MULTIMODAL_MODEL_AUDIO", "minicpm-o")

        start_time = time.monotonic()
        request_id = headers.get("X-Request-ID", "")

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                response = await client.post(
                    tts_url,
                    json={
                        "model": model,
                        "input": text,
                        "voice": voice,
                    },
                    headers=headers,
                )

                duration = time.monotonic() - start_time

                if response.status_code == 200:
                    logger.info(
                        f"TTS 合成成功: user_id={user_id}, "
                        f"message_uuid={message_uuid}, "
                        f"audio_size={len(response.content)}"
                    )
                    record_gateway_span(
                        request_type="tts",
                        model=model,
                        duration=duration,
                        status_code=200,
                        request_id=request_id,
                    )
                    return response.content

                # 记录失败 span
                record_gateway_span(
                    request_type="tts",
                    model=model,
                    duration=duration,
                    status_code=response.status_code,
                    request_id=request_id,
                    error=f"Gateway HTTP {response.status_code}",
                )

                # 处理 Gateway 错误响应
                TTSService._handle_gateway_error(response)

        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            logger.warning(
                f"TTS 合成超时: user_id={user_id}, "
                f"message_uuid={message_uuid}, timeout={timeout}s"
            )
            record_gateway_span(
                request_type="tts",
                model=model,
                duration=duration,
                status_code=504,
                request_id=request_id,
                error="timeout",
            )
            raise TTSError(
                code="TTS_TIMEOUT",
                message="语音合成超时，请稍后重试",
                status_code=504,
                data={"gateway_error": "E3003"},
            )
        except TTSError:
            raise
        except httpx.ConnectError:
            duration = time.monotonic() - start_time
            logger.error(
                f"TTS 连接失败: user_id={user_id}, message_uuid={message_uuid}"
            )
            record_gateway_span(
                request_type="tts",
                model=model,
                duration=duration,
                status_code=503,
                request_id=request_id,
                error="connect_error",
            )
            raise TTSError(
                code="TTS_SERVICE_UNAVAILABLE",
                message="语音合成服务暂时不可用，请稍后重试",
                status_code=503,
            )
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(
                f"TTS 合成异常: user_id={user_id}, "
                f"message_uuid={message_uuid}, error={e}"
            )
            record_gateway_span(
                request_type="tts",
                model=model,
                duration=duration,
                status_code=503,
                request_id=request_id,
                error=str(e),
            )
            raise TTSError(
                code="TTS_SERVICE_UNAVAILABLE",
                message="语音合成服务暂时不可用，请稍后重试",
                status_code=503,
            )

        # 不应到达这里，_handle_gateway_error 总会 raise
        raise TTSError(
            code="TTS_SERVICE_UNAVAILABLE",
            message="语音合成服务暂时不可用，请稍后重试",
            status_code=503,
        )

    @staticmethod
    def _handle_gateway_error(response: httpx.Response) -> None:
        """处理 Gateway 错误响应

        E3001 → 404 模型不存在
        E3002 有 retry_after → TTS_MODEL_SWITCHING
        E3002 无 retry_after → TTS_SERVICE_UNAVAILABLE
        E3003 → 504 超时
        """
        try:
            body = response.json()
            error_info = body.get("error", {})
            error_code = error_info.get("code", "")
            details = error_info.get("details", {})
        except Exception:
            error_code = ""
            details = {}

        if error_code == "E3001":
            raise TTSError(
                code="TTS_MODEL_NOT_FOUND",
                message="TTS 模型不存在",
                status_code=404,
                data={"gateway_error": "E3001"},
            )
        elif error_code == "E3002":
            retry_after = details.get("retry_after")
            if retry_after is not None:
                raise TTSError(
                    code="TTS_MODEL_SWITCHING",
                    message="模型正在切换中，请稍后重试",
                    status_code=503,
                    data={
                        "gateway_error": "E3002",
                        "estimated_wait_seconds": retry_after,
                        "retry_after": retry_after,
                    },
                )
            else:
                raise TTSError(
                    code="TTS_SERVICE_UNAVAILABLE",
                    message="语音合成服务暂时不可用，请稍后重试",
                    status_code=503,
                    data={"gateway_error": "E3002"},
                )
        elif error_code == "E3003":
            raise TTSError(
                code="TTS_TIMEOUT",
                message="语音合成超时，请稍后重试",
                status_code=504,
                data={"gateway_error": "E3003"},
            )
        else:
            raise TTSError(
                code="TTS_SERVICE_UNAVAILABLE",
                message="语音合成服务暂时不可用，请稍后重试",
                status_code=503,
                data={"gateway_error": error_code} if error_code else None,
            )


# 单例实例
tts_service = TTSService()
