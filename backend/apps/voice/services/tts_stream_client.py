import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from django.conf import settings

from apps.voice.services.ws_client_base import BaseWSClient

logger = logging.getLogger(__name__)


class TTSStreamClient(BaseWSClient):
    def __init__(self, on_audio: Callable[[bytes], Awaitable[None]],
                 on_sentence_start: Optional[Callable[[int, str], Awaitable[None]]] = None,
                 on_done: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        super().__init__()
        self._on_audio = on_audio
        self._on_sentence_start = on_sentence_start
        self._on_done = on_done
        self._done_event: asyncio.Event = asyncio.Event()
        self._sample_rate: int = 24000

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def connect(self) -> str:
        url = f"{settings.VOICE_TTS_URL}?api_key={settings.LLM_GATEWAY_API_KEY}"
        event = await self._connect_ws(url, close_timeout=5)
        if event.get("type") != "session.created":
            raise RuntimeError(f"TTS WS unexpected first event: {event}")
        self._sample_rate = event.get("sample_rate", 24000)
        self._done_event.clear()
        logger.info("TTS WS connected: session_id=%s, sample_rate=%d", self._session_id, self._sample_rate)
        return self._session_id

    async def configure(self, voice: Optional[str] = None, speed: Optional[float] = None) -> None:
        msg: dict[str, Any] = {"type": "config"}
        if voice: msg["voice"] = voice
        if speed: msg["speed"] = speed
        await self._send_json_msg(msg)

    async def send_text_delta(self, text: str) -> None:
        await self._send_json_msg({"type": "text.delta", "delta": text})

    async def send_text_done(self) -> None:
        await self._send_json_msg({"type": "text.done"})

    async def wait_for_done(self, timeout: Optional[float] = None) -> None:
        await asyncio.wait_for(self._done_event.wait(), timeout=timeout or settings.VOICE_TTS_TIMEOUT)

    async def disconnect(self) -> None:
        await super().disconnect()
        logger.info("TTS WS disconnected: session_id=%s", self._session_id)

    async def _handle_message(self, msg: Any) -> None:
        if isinstance(msg, bytes):
            await self._on_audio(msg)
            return
        event = json.loads(msg)
        event_type = event.get("type", "")
        if event_type == "tts.sentence_start" and self._on_sentence_start:
            await self._on_sentence_start(event.get("sentence_idx", 0), event.get("text", ""))
        elif event_type == "audio.done":
            self._done_event.set()
            if self._on_done: await self._on_done()
            logger.info("TTS audio.done received")
        elif event_type == "error":
            logger.warning("TTS error: %s", event.get("message", ""))

    async def _on_connection_lost(self, err) -> None:
        self._done_event.set()

    async def _on_error(self, err: Exception) -> None:
        self._done_event.set()
