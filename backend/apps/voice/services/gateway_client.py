"""llmgateway WebSocket 客户端

参考:
- docs/voice-capability-requirements.md#6 WebSocket 持续监控模式
- specs/009-voice-interaction/research.md#5 上游 llmgateway WebSocket 连接

职责：管理与 llmgateway 的 WebSocket 长连接，负责音频帧转发和事件接收分发。
"""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Optional

import websockets
from django.conf import settings

from apps.common.exceptions import (
    ExternalServiceError,
    LLMConnectionError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMInvalidResponseError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)

# 事件回调类型：接收 event dict，返回 coroutine
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

# llmgateway 错误码 → 宪法异常映射
_GATEWAY_ERROR_MAP: dict[str, type[Exception]] = {
    "CONNECTION_FAILED": LLMConnectionError,
    "CONNECT_TIMEOUT": LLMConnectionError,
    "TIMEOUT": LLMTimeoutError,
    "INFERENCE_TIMEOUT": LLMTimeoutError,
    "RATE_LIMIT": LLMRateLimitError,
    "RATE_LIMITED": LLMRateLimitError,
    "CONTENT_FILTER": LLMContentFilterError,
    "CONTENT_BLOCKED": LLMContentFilterError,
    "INVALID_RESPONSE": LLMInvalidResponseError,
    "MODEL_ERROR": LLMInvalidResponseError,
    "CONTEXT_LENGTH": LLMContextLengthError,
    "CONTEXT_TOO_LONG": LLMContextLengthError,
    "INPUT_TOO_LONG": LLMContextLengthError,
    "QUOTA_EXCEEDED": LLMQuotaExceededError,
}


class GatewayClient:
    """llmgateway WebSocket 客户端

    管理与 llmgateway 的持久 WebSocket 连接。
    每个 VoiceConsumer 实例拥有一个 GatewayClient 实例。

    生命周期：
    1. connect() - 建立连接，等待 session.created
    2. configure() - 发送 session.configure
    3. send_audio() - 转发 PCM16 音频帧
    4. disconnect() - 断开连接
    """

    def __init__(
        self,
        on_event: EventCallback,
        user_id: int,
    ) -> None:
        self._on_event = on_event
        self._user_id = user_id
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._connected = False
        self._session_id: Optional[str] = None
        self._last_config: Optional[dict[str, Any]] = None
        self._reconnecting = False

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def connect(self) -> bool:
        """建立到 llmgateway 的 WebSocket 连接

        Returns:
            True 连接成功并收到 session.created，False 失败
        """
        gateway_url = settings.LLM_GATEWAY_WS_URL
        api_key = settings.LLM_GATEWAY_WS_API_KEY

        ws_url = f"{gateway_url}/v1/voice/stream?api_key={api_key}"

        try:
            logger.info(
                "Gateway connecting: user_id=%s, url=%s",
                self._user_id,
                gateway_url,
            )
            self._ws = await websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=60,
                close_timeout=5,
            )

            # 等待 session.created（超时 10 秒）
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            event = json.loads(raw)
            if event.get("type") != "session.created":
                logger.error(
                    "Gateway unexpected first event: %s", event.get("type")
                )
                await self._close_ws()
                return False

            self._session_id = event.get("data", {}).get("session_id")
            self._connected = True

            # 启动接收循环
            self._receive_task = asyncio.create_task(self._receive_loop())

            logger.info(
                "Gateway connected: user_id=%s, session_id=%s",
                self._user_id,
                self._session_id,
            )
            return True

        except (
            websockets.exceptions.WebSocketException,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            logger.error(
                "Gateway connect failed: user_id=%s, error=%s", self._user_id, e
            )
            await self._close_ws()
            return False

    async def configure(self, config: dict[str, Any]) -> bool:
        """发送 session.configure 到 llmgateway

        Args:
            config: 会话配置参数（vad_threshold, speaker_identify, auto_respond 等）

        Returns:
            True 配置成功（收到 session.configured），False 失败
        """
        if not self.connected:
            return False

        self._last_config = config
        msg = json.dumps({"type": "session.configure", "data": config})
        try:
            await self._ws.send(msg)
            logger.info(
                "Gateway session.configure sent: user_id=%s, config=%s",
                self._user_id,
                config,
            )
            return True
        except websockets.exceptions.WebSocketException as e:
            logger.error(
                "Gateway configure failed: user_id=%s, error=%s",
                self._user_id,
                e,
            )
            return False

    async def send_audio(self, pcm_data: bytes) -> bool:
        """发送 PCM16 音频帧到 llmgateway（Binary 帧）

        Args:
            pcm_data: 原始 PCM16 音频数据（无 WAV 头）
        """
        if not self.connected:
            return False

        try:
            await self._ws.send(pcm_data)
            return True
        except websockets.exceptions.WebSocketException:
            self._connected = False
            return False

    async def send_json(self, message: dict[str, Any]) -> bool:
        """发送 JSON 控制消息到 llmgateway

        Args:
            message: JSON 消息（如 response.cancel, input.commit）
        """
        if not self.connected:
            return False

        try:
            await self._ws.send(json.dumps(message))
            logger.debug(
                "Gateway JSON sent: user_id=%s, type=%s",
                self._user_id,
                message.get("type"),
            )
            return True
        except websockets.exceptions.WebSocketException as e:
            logger.error(
                "Gateway send_json failed: user_id=%s, error=%s",
                self._user_id,
                e,
            )
            self._connected = False
            return False

    async def cancel_response(self, response_id: str) -> bool:
        """发送 response.cancel 中断推理

        Args:
            response_id: 要中断的推理 response_id
        """
        return await self.send_json({
            "type": "response.cancel",
            "data": {"response_id": response_id},
        })

    async def reconnect(self) -> bool:
        """断开后重连 llmgateway（仅尝试一次）

        在 _receive_loop 检测到连接断开时调用。
        重连成功后自动重新发送上次的 session.configure。

        Returns:
            True 重连成功，False 失败
        """
        if self._reconnecting:
            return False
        self._reconnecting = True
        try:
            logger.info(
                "Gateway reconnecting: user_id=%s, session_id=%s",
                self._user_id,
                self._session_id,
            )
            await self._close_ws()
            self._connected = False

            ok = await self.connect()
            if not ok:
                logger.warning(
                    "Gateway reconnect failed: user_id=%s", self._user_id
                )
                return False

            # 重连成功，重新发送配置
            if self._last_config:
                await self.configure(self._last_config)

            logger.info(
                "Gateway reconnected: user_id=%s, new_session=%s",
                self._user_id,
                self._session_id,
            )
            return True
        finally:
            self._reconnecting = False

    async def disconnect(self) -> None:
        """断开 llmgateway WebSocket 连接"""
        logger.info("Gateway disconnecting: user_id=%s", self._user_id)
        self._connected = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        await self._close_ws()
        logger.info("Gateway disconnected: user_id=%s", self._user_id)

    async def _receive_loop(self) -> None:
        """接收 llmgateway 事件的后台循环"""
        try:
            async for raw_message in self._ws:
                if isinstance(raw_message, str):
                    # JSON 事件
                    try:
                        event = json.loads(raw_message)
                        await self._on_event(event)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Gateway invalid JSON: user_id=%s", self._user_id
                        )
                # Binary 帧暂不处理（llmgateway 当前不发送 binary 下行）

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(
                "Gateway connection closed: user_id=%s, code=%s, reason=%s",
                self._user_id,
                e.code,
                e.reason,
            )
            # 非主动断开时尝试一次重连
            if not self._reconnecting:
                asyncio.create_task(self.reconnect())
                return
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Gateway receive loop error: user_id=%s", self._user_id
            )
        finally:
            self._connected = False

    @staticmethod
    def map_gateway_error(
        code: str, message: str = "", recoverable: bool = True
    ) -> dict[str, Any]:
        """将 llmgateway 错误码映射到宪法异常体系

        Returns:
            dict 包含 mapped_code, mapped_message, should_retry, max_retries,
            retry_after (仅 RateLimit), recoverable
        """
        exc_cls = _GATEWAY_ERROR_MAP.get(code)

        if exc_cls is None:
            # 未识别的错误码 → ExternalServiceError
            return {
                "mapped_code": "EXTERNAL_SERVICE_ERROR",
                "mapped_message": message or "外部服务异常",
                "should_retry": False,
                "max_retries": 0,
                "recoverable": recoverable,
            }

        exc = exc_cls(message) if message else exc_cls()
        result: dict[str, Any] = {
            "mapped_code": exc.error_code,
            "mapped_message": exc.message,
            "should_retry": getattr(exc, "should_retry", False),
            "max_retries": getattr(exc, "max_retries", 0),
            "recoverable": recoverable,
        }
        if isinstance(exc, LLMRateLimitError):
            result["retry_after"] = exc.retry_after

        return result

    async def _close_ws(self) -> None:
        """关闭底层 WebSocket 连接"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
