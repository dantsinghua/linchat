import json
import logging
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Any, Callable

from django.conf import settings
from django.utils import timezone

from apps.users.crypto import generate_token_hash, sm4_decrypt
from core.redis import get_token_key, redis_delete, redis_expire, redis_get

logger = logging.getLogger(__name__)

TOKEN_COOKIE_NAME = "linchat_token"
WS_CLOSE_AUTH_FAILED = 4001


class WebSocketTokenAuthMiddleware:
    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "websocket":
            await self.app(scope, receive, send); return
        token = self._extract_token_from_headers(scope)
        if not token:
            # 无 Cookie token 时放行，交给 Consumer 处理设备 token 认证（query string）
            logger.debug("WebSocket 无 Cookie token，交给 Consumer 认证")
            await self.app(scope, receive, send); return
        try:
            user_info = await self._verify_token_async(token)
        except _WebSocketAuthError as e:
            logger.warning("WebSocket 认证失败: %s", e)
            await self._close_websocket(send); return
        except Exception as e:
            logger.warning("WebSocket 认证异常: %s", e)
            await self._close_websocket(send); return
        scope["user_id"] = user_info["user_id"]
        scope["username"] = user_info["username"]
        scope["user_type"] = user_info.get("user_type", "user")
        logger.debug("WebSocket 认证成功: user_id=%s, username=%s", user_info["user_id"], user_info["username"])
        await self.app(scope, receive, send)

    def _extract_token_from_headers(self, scope: dict[str, Any]) -> str | None:
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"")
        if not cookie_header: return None
        cookie_str = cookie_header.decode("utf-8", errors="replace")
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_str)
        except Exception:
            logger.warning("WebSocket Cookie 解析失败"); return None
        morsel = cookie.get(TOKEN_COOKIE_NAME)
        if morsel is None: return None
        return morsel.value or None

    async def _verify_token_async(self, token: str) -> dict[str, Any]:
        try:
            sm4_decrypt(token)
        except Exception:
            raise _WebSocketAuthError("Token 无效（SM4 解密失败）")
        token_hash = generate_token_hash(token)
        token_key = get_token_key(token_hash)
        token_data = await redis_get(token_key)
        if not token_data:
            raise _WebSocketAuthError("登录已过期，请重新登录")
        try:
            token_info: dict[str, Any] = json.loads(token_data)
        except (json.JSONDecodeError, TypeError):
            raise _WebSocketAuthError("Token 数据损坏")
        login_time_str = token_info.get("login_time")
        if not login_time_str:
            raise _WebSocketAuthError("Token 数据损坏（缺少 login_time）")
        login_time = datetime.fromisoformat(login_time_str)
        if login_time.tzinfo is None:
            login_time = timezone.make_aware(login_time)
        now = timezone.now()
        elapsed = (now - login_time).total_seconds()
        if elapsed >= settings.AUTH_TOKEN_ABSOLUTE_TTL:
            await redis_delete(token_key)
            raise _WebSocketAuthError("登录已超过24小时，请重新登录")
        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        await redis_expire(token_key, ttl)
        return token_info

    async def _close_websocket(self, send: Callable) -> None:
        try:
            await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILED})
        except Exception as e:
            logger.debug("发送 WebSocket 关闭帧异常（可忽略）: %s", e)


class _WebSocketAuthError(Exception):
    pass
