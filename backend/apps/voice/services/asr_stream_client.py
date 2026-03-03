"""Gateway ASR WebSocket 流式客户端

连接 Gateway ASR WebSocket 端点，接收 VAD/转录事件。
每个语音会话创建一个实例，会话结束后关闭。

参考: specs/010-voice-agent-pipeline/contracts/gateway-asr-ws.md 第 1 节
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import websockets
import websockets.exceptions
from django.conf import settings

logger = logging.getLogger(__name__)


class ASRStreamClient:
    """Gateway ASR WebSocket 流式客户端"""

    def __init__(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected: bool = False
        self._on_event = on_event
        self._api_key: str = settings.LLM_GATEWAY_API_KEY
        self._session_id: Optional[str] = None
        self._recv_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def connect(self) -> str:
        """建立 ASR WebSocket 连接，返回 session_id。"""
        url = f"{settings.VOICE_ASR_WS_URL}?api_key={self._api_key}"
        self._ws = await websockets.connect(
            url, ping_interval=30, ping_timeout=60, close_timeout=5
        )
        # 等待 session.created
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(raw)
        self._session_id = event["session_id"]
        self._connected = True
        # 启动接收循环
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info(
            "ASR WS connected: session_id=%s", self._session_id
        )
        return self._session_id

    async def configure(
        self,
        auto_commit: bool = True,
        speech_pad_ms: Optional[int] = None,
        language: Optional[str] = None,
    ) -> None:
        """发送 ASR 配置消息。"""
        msg: dict[str, Any] = {
            "type": "configure",
            "auto_commit": auto_commit,
            "speech_pad_ms": speech_pad_ms or settings.VOICE_ASR_SPEECH_PAD_MS,
            "language": language or settings.VOICE_ASR_LANGUAGE,
        }
        await self._ws.send(json.dumps(msg))
        logger.info(
            "ASR configured: auto_commit=%s, pad=%s, lang=%s",
            auto_commit,
            msg["speech_pad_ms"],
            msg["language"],
        )

    async def send_audio(self, pcm_data: bytes) -> None:
        """转发 PCM 音频帧（binary）。"""
        if self._ws and self._connected:
            await self._ws.send(pcm_data)

    async def send_commit(self) -> None:
        """手动触发转录（超时安全网）。"""
        if self._ws and self._connected:
            await self._ws.send(json.dumps({"type": "commit"}))
            logger.debug("ASR commit sent")

    async def disconnect(self) -> None:
        """关闭连接。"""
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("ASR WS disconnected: session_id=%s", self._session_id)

    async def _receive_loop(self) -> None:
        """接收 Gateway ASR 事件并回调。"""
        try:
            async for message in self._ws:
                if isinstance(message, str):
                    event = json.loads(message)
                    logger.debug("ASR event: type=%s", event.get("type"))
                    await self._on_event(event)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(
                "ASR WS closed: code=%s, reason=%s", e.code, e.reason
            )
            self._connected = False
            await self._on_event(
                {
                    "type": "error",
                    "message": f"ASR 连接断开: {e.code}",
                    "code": "CONNECTION_CLOSED",
                }
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ASR receive error: %s", e, exc_info=True)
            self._connected = False
            await self._on_event(
                {
                    "type": "error",
                    "message": f"ASR 接收异常: {e}",
                    "code": "RECEIVE_ERROR",
                }
            )
