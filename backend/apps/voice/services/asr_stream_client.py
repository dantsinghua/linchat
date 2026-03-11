import json
import logging
from typing import Any, Callable, Optional

from django.conf import settings

from apps.voice.services.ws_client_base import BaseWSClient

logger = logging.getLogger(__name__)


class ASRStreamClient(BaseWSClient):
    def __init__(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        super().__init__()
        self._on_event = on_event

    async def connect(self) -> str:
        url = f"{settings.VOICE_ASR_WS_URL}?api_key={settings.LLM_GATEWAY_API_KEY}"
        await self._connect_ws(url, ping_interval=30, ping_timeout=60, close_timeout=5)
        logger.info("ASR WS connected: session_id=%s", self._session_id)
        return self._session_id

    async def configure(self, auto_commit: bool = True, speech_pad_ms: Optional[int] = None, language: Optional[str] = None) -> None:
        msg = {"type": "configure", "auto_commit": auto_commit,
               "speech_pad_ms": speech_pad_ms or settings.VOICE_ASR_SPEECH_PAD_MS,
               "language": language or settings.VOICE_ASR_LANGUAGE}
        await self._send_json_msg(msg)
        logger.info("ASR configured: auto_commit=%s, pad=%s, lang=%s", auto_commit, msg["speech_pad_ms"], msg["language"])

    async def send_audio(self, pcm_data: bytes) -> None:
        await self._send_bytes_msg(pcm_data)

    async def send_commit(self) -> None:
        await self._send_json_msg({"type": "commit"})

    async def disconnect(self) -> None:
        await super().disconnect()
        logger.info("ASR WS disconnected: session_id=%s", self._session_id)

    async def _handle_message(self, msg: Any) -> None:
        if isinstance(msg, str):
            await self._on_event(json.loads(msg))

    async def _on_connection_lost(self, err) -> None:
        await self._on_event({"type": "error", "message": f"ASR 连接断开: {err.code}", "code": "CONNECTION_CLOSED"})

    async def _on_error(self, err: Exception) -> None:
        await self._on_event({"type": "error", "message": f"ASR 接收异常: {err}", "code": "RECEIVE_ERROR"})
