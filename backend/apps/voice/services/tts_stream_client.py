"""Gateway TTS 流式 WebSocket 客户端

每次 VoicePipeline.run_pipeline() 创建一个实例，pipeline 结束后关闭。
Gateway 自动分句合成（句号/问号立即切 + 逗号 30 字符后切 + 200 字符强制切），
客户端无需实现 split_sentences() 分句逻辑。

参考: docs/tts-websocket-api.md, specs/010-voice-agent-pipeline/contracts/gateway-asr-ws.md 第 6 节
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import websockets
import websockets.exceptions
from django.conf import settings

from apps.voice.services.asr_stream_client import cleanup_ws_connection

logger = logging.getLogger(__name__)


class TTSStreamClient:
    """Gateway TTS 流式 WebSocket 客户端"""

    def __init__(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_sentence_start: Optional[Callable[[int, str], Awaitable[None]]] = None,
        on_done: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._on_audio = on_audio
        self._on_sentence_start = on_sentence_start
        self._on_done = on_done
        self._api_key: str = settings.LLM_GATEWAY_API_KEY
        self._connected: bool = False
        self._done_event: asyncio.Event = asyncio.Event()
        self._session_id: Optional[str] = None
        self._sample_rate: int = 24000
        self._recv_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def connect(self) -> str:
        """建立 TTS WS 连接，返回 session_id。"""
        url = f"{settings.VOICE_TTS_URL}?api_key={self._api_key}"
        self._ws = await websockets.connect(url, close_timeout=5)
        # 等待 session.created
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(raw)
        if event.get("type") != "session.created":
            raise RuntimeError(f"TTS WS unexpected first event: {event}")
        self._session_id = event["session_id"]
        self._sample_rate = event.get("sample_rate", 24000)
        self._connected = True
        self._done_event.clear()
        # 启动接收循环
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info(
            "TTS WS connected: session_id=%s, sample_rate=%d",
            self._session_id,
            self._sample_rate,
        )
        return self._session_id

    async def configure(
        self,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> None:
        """配置声音和语速（可选）。"""
        msg: dict[str, Any] = {"type": "config"}
        if voice:
            msg["voice"] = voice
        if speed:
            msg["speed"] = speed
        await self._ws.send(json.dumps(msg))
        logger.info("TTS configured: voice=%s, speed=%s", voice, speed)

    async def send_text_delta(self, text: str) -> None:
        """发送文本增量（Agent 每个 content chunk 调用一次）。"""
        if self._ws and self._connected:
            await self._ws.send(
                json.dumps({"type": "text.delta", "delta": text})
            )

    async def send_text_done(self) -> None:
        """通知文本输入完毕，Gateway 会 flush 剩余缓冲。"""
        if self._ws and self._connected:
            await self._ws.send(json.dumps({"type": "text.done"}))

    async def wait_for_done(self, timeout: Optional[float] = None) -> None:
        """等待 audio.done 信号。"""
        t = timeout or settings.VOICE_TTS_TIMEOUT
        await asyncio.wait_for(self._done_event.wait(), timeout=t)

    async def disconnect(self) -> None:
        """关闭连接。"""
        self._connected = False
        await cleanup_ws_connection(self._ws, self._recv_task)
        self._ws = None
        logger.info("TTS WS disconnected: session_id=%s", self._session_id)

    async def _receive_loop(self) -> None:
        """接收 TTS 事件和音频帧。"""
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    # PCM 音频帧 → 回调转发前端
                    await self._on_audio(msg)
                else:
                    event = json.loads(msg)
                    event_type = event.get("type", "")

                    if event_type == "tts.sentence_start":
                        logger.debug(
                            "TTS sentence_start: idx=%s, text=%s",
                            event.get("sentence_idx"),
                            event.get("text", "")[:30],
                        )
                        if self._on_sentence_start:
                            await self._on_sentence_start(
                                event.get("sentence_idx", 0),
                                event.get("text", ""),
                            )

                    elif event_type == "tts.sentence_end":
                        logger.debug(
                            "TTS sentence_end: idx=%s",
                            event.get("sentence_idx"),
                        )

                    elif event_type == "audio.done":
                        self._done_event.set()
                        if self._on_done:
                            await self._on_done()
                        logger.info("TTS audio.done received")
                        break

                    elif event_type == "error":
                        logger.warning(
                            "TTS error: %s", event.get("message", "")
                        )

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("TTS WS closed: code=%s, reason=%s", e.code, e.reason)
            self._connected = False
            self._done_event.set()  # 不阻塞 pipeline
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("TTS receive error: %s", e, exc_info=True)
            self._connected = False
            self._done_event.set()
