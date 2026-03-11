import asyncio
import json
import logging
from typing import Any, Optional

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)


async def cleanup_ws_connection(ws, recv_task) -> None:
    if recv_task and not recv_task.done():
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass
    if ws:
        try:
            await ws.close()
        except Exception:
            pass


class BaseWSClient:
    def __init__(self) -> None:
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected: bool = False
        self._session_id: Optional[str] = None
        self._recv_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def _connect_ws(self, url: str, **ws_kwargs) -> dict:
        self._ws = await websockets.connect(url, **ws_kwargs)
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(raw)
        self._session_id = event.get("session_id")
        self._connected = True
        self._recv_task = asyncio.create_task(self._receive_loop())
        return event

    async def disconnect(self) -> None:
        self._connected = False
        await cleanup_ws_connection(self._ws, self._recv_task)
        self._ws = None

    async def _send_json_msg(self, data: dict[str, Any]) -> None:
        if self._ws and self._connected:
            await self._ws.send(json.dumps(data))

    async def _send_bytes_msg(self, data: bytes) -> None:
        if self._ws and self._connected:
            await self._ws.send(data)

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._ws:
                await self._handle_message(msg)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("%s WS closed: code=%s, reason=%s", self.__class__.__name__, e.code, e.reason)
            self._connected = False
            await self._on_connection_lost(e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("%s receive error: %s", self.__class__.__name__, e, exc_info=True)
            self._connected = False
            await self._on_error(e)

    async def _handle_message(self, msg: Any) -> None:
        raise NotImplementedError

    async def _on_connection_lost(self, err: websockets.exceptions.ConnectionClosed) -> None:
        pass

    async def _on_error(self, err: Exception) -> None:
        pass
