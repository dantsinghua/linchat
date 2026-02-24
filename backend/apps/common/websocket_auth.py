"""WebSocket Token 认证中间件

替代 Django Channels 的 AuthMiddlewareStack，
兼容 LinChat 的 SM4 Token-in-Cookie 认证机制。

认证流程：
1. 从 scope['headers'] 解析 Cookie 获取 linchat_token
2. SM4 解密验证 → SHA256 Hash → Redis 查询
3. 24h 绝对过期检查 + TTL 刷新
4. 设置 scope['user_id']/scope['username']/scope['user_type']
"""

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

# WebSocket 关闭码：认证失败
WS_CLOSE_AUTH_FAILED = 4001


class WebSocketTokenAuthMiddleware:
    """WebSocket Token 认证中间件

    ASGI middleware，用于替代 Django Channels 的 AuthMiddlewareStack，
    兼容 LinChat 的 SM4 Token-in-Cookie 认证机制。

    认证流程：
    1. 从 scope['headers'] 解析 Cookie 获取 linchat_token
    2. SM4 解密验证 → SHA256 Hash → Redis 查询
    3. 24h 绝对过期检查 + TTL 刷新
    4. 设置 scope['user_id']/scope['username']/scope['user_type']

    认证失败时发送 websocket.close(code=4001) 关闭连接。

    注意：
    - 不处理设备 API Token 认证（由 consumer connect 中处理）
    - 仅处理 scope["type"] == "websocket" 的连接
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable,
        send: Callable,
    ) -> None:
        """ASGI 入口，拦截 WebSocket 连接进行 Token 认证。

        Args:
            scope: ASGI 连接作用域。
            receive: ASGI 接收回调。
            send: ASGI 发送回调。
        """
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        token = self._extract_token_from_headers(scope)
        if not token:
            logger.warning("WebSocket 认证失败: Cookie 中未找到 linchat_token")
            await self._close_websocket(send)
            return

        try:
            user_info = await self._verify_token_async(token)
        except _WebSocketAuthError as e:
            logger.warning("WebSocket 认证失败: %s", e)
            await self._close_websocket(send)
            return
        except Exception as e:
            logger.warning("WebSocket 认证异常: %s", e)
            await self._close_websocket(send)
            return

        scope["user_id"] = user_info["user_id"]
        scope["username"] = user_info["username"]
        scope["user_type"] = user_info.get("user_type", "user")

        logger.debug(
            "WebSocket 认证成功: user_id=%s, username=%s",
            user_info["user_id"],
            user_info["username"],
        )

        await self.app(scope, receive, send)

    def _extract_token_from_headers(
        self, scope: dict[str, Any]
    ) -> str | None:
        """从 ASGI scope headers 中解析 Cookie 提取 linchat_token。

        ASGI scope 中 headers 为 list of (name, value) 二元组，
        name 和 value 均为 bytes 类型。

        Args:
            scope: ASGI 连接作用域。

        Returns:
            linchat_token 值，未找到时返回 None。
        """
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"")
        if not cookie_header:
            return None

        cookie_str = cookie_header.decode("utf-8", errors="replace")
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_str)
        except Exception:
            logger.warning("WebSocket Cookie 解析失败")
            return None

        morsel = cookie.get(TOKEN_COOKIE_NAME)
        if morsel is None:
            return None

        return morsel.value or None

    async def _verify_token_async(self, token: str) -> dict[str, Any]:
        """异步验证 Token 有效性。

        验证步骤：
        1. SM4 解密验证 Token 格式
        2. 计算 SHA256 Hash
        3. 从 Redis 获取 Token 数据
        4. 检查 24h 绝对过期
        5. 刷新无操作 TTL

        Args:
            token: 从 Cookie 中提取的 linchat_token 原文。

        Returns:
            Token 信息字典，包含 user_id、username、user_type 等。

        Raises:
            _WebSocketAuthError: Token 无效或已过期。
        """
        # 1. SM4 解密验证格式有效性
        try:
            sm4_decrypt(token)
        except Exception:
            raise _WebSocketAuthError("Token 无效（SM4 解密失败）")

        # 2. 计算 SHA256 Hash
        token_hash = generate_token_hash(token)

        # 3. 从 Redis 获取 Token 数据
        token_key = get_token_key(token_hash)
        token_data = await redis_get(token_key)

        if not token_data:
            raise _WebSocketAuthError("登录已过期，请重新登录")

        # 4. 解析 Token JSON 数据
        try:
            token_info: dict[str, Any] = json.loads(token_data)
        except (json.JSONDecodeError, TypeError):
            raise _WebSocketAuthError("Token 数据损坏")

        # 5. 检查 24h 绝对过期
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

        # 6. 刷新无操作 TTL（取 idle_ttl 和剩余绝对时间的较小值）
        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        await redis_expire(token_key, ttl)

        return token_info

    async def _close_websocket(self, send: Callable) -> None:
        """发送 WebSocket 关闭帧（code=4001 认证失败）。

        按照 WebSocket 协议，需要先 accept 再 close，
        否则某些客户端无法收到关闭码。

        Args:
            send: ASGI 发送回调。
        """
        try:
            await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILED})
        except Exception as e:
            logger.debug("发送 WebSocket 关闭帧异常（可忽略）: %s", e)


class _WebSocketAuthError(Exception):
    """WebSocket 认证内部异常（仅在中间件内部使用）。"""

    pass
