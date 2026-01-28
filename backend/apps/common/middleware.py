"""
认证中间件

参考:
- behavior-model.md#1.3 Token鉴权验证
- rule-model.md#R_TOKEN_002 Token有效性校验规则
- rule-model.md#R_TOKEN_003 Token双重过期规则

安全要求:
- Token 必须存储在 httpOnly Cookie 中
- 禁止使用 localStorage 或 Authorization 头传递 Token
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from apps.common.exceptions import TokenExpiredException
from apps.users.crypto import generate_token_hash, sm4_decrypt
from core.redis import (
    get_token_key,
    get_user_token_key,
    redis_delete,
    redis_expire,
    redis_get,
    sync_redis_delete,
    sync_redis_expire,
    sync_redis_get,
)

logger = logging.getLogger(__name__)


# 不需要认证的路径列表
PUBLIC_PATHS = [
    "/api/v1/auth/captcha",
    "/api/v1/auth/login",
    "/api/v1/health/",
    "/admin/",
    "/static/",
]

# Cookie 名称
TOKEN_COOKIE_NAME = "linchat_token"


class TokenAuthMiddleware:
    """
    Token 认证中间件（纯同步版本）

    实现 R_TOKEN_003 双重过期规则：
    - 无操作过期: 1小时无操作自动过期，有操作时刷新TTL
    - 绝对过期: 登录后24小时强制失效，刷新操作不延长此期限

    注意: 此中间件是纯同步的，Django 会自动在线程池中运行它来支持异步视图
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """同步调用"""
        # 检查是否为公开路径
        if self._is_public_path(request.path):
            return self.get_response(request)

        # 从 httpOnly Cookie 获取 Token
        token = request.COOKIES.get(TOKEN_COOKIE_NAME)

        if not token:
            return self._unauthorized_response("请先登录")

        # 验证 Token
        try:
            user_info = self._verify_token_sync(token)
            # 将用户信息附加到 request
            request.user_id = user_info["user_id"]
            request.username = user_info["username"]
            request.token_hash = generate_token_hash(token)
        except TokenExpiredException as e:
            return self._unauthorized_response(str(e))
        except Exception as e:
            logger.warning(f"Token verification failed: {e}")
            return self._unauthorized_response("认证失败")

        return self.get_response(request)

    def _is_public_path(self, path: str) -> bool:
        """检查是否为公开路径（不需要认证）"""
        for public_path in PUBLIC_PATHS:
            if path.startswith(public_path):
                return True
        return False

    def _verify_token_sync(self, token: str) -> dict:
        """
        同步验证 Token（纯同步版本，使用同步 Redis 客户端）

        参考: behavior-model.md#1.3 Token鉴权验证
        """
        # [R_TOKEN_002] Token 有效性校验
        if not token:
            raise TokenExpiredException("请先登录")

        # 尝试解密验证 Token 格式
        try:
            sm4_decrypt(token)
        except Exception:
            raise TokenExpiredException("Token无效")

        # 计算 Token 哈希
        token_hash = generate_token_hash(token)
        token_key = get_token_key(token_hash)

        # 从 Redis 获取 Token 信息（使用同步 Redis 客户端）
        token_data = sync_redis_get(token_key)

        if not token_data:
            raise TokenExpiredException("登录已过期，请重新登录")

        token_info = json.loads(token_data)
        login_time_str = token_info.get("login_time")

        if not login_time_str:
            raise TokenExpiredException("Token数据损坏")

        login_time = datetime.fromisoformat(login_time_str)
        # 确保 login_time 是 aware datetime
        if login_time.tzinfo is None:
            login_time = timezone.make_aware(login_time)

        now = timezone.now()

        # [R_TOKEN_003] 检查24小时绝对过期
        elapsed = (now - login_time).total_seconds()
        if elapsed >= settings.AUTH_TOKEN_ABSOLUTE_TTL:  # 24小时
            sync_redis_delete(token_key)
            raise TokenExpiredException("登录已超过24小时，请重新登录")

        # [R_TOKEN_003] 刷新TTL（1小时无操作过期，但不超过24小时边界）
        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        sync_redis_expire(token_key, ttl)

        # 更新最后活跃时间
        token_info["last_active_time"] = now.isoformat()
        # 注意：这里不重新保存整个 token_info，只刷新 TTL 即可
        # 如果需要更新 last_active_time，需要重新 SETEX

        return token_info

    async def _verify_token(self, token: str) -> dict:
        """
        异步验证 Token（用于异步上下文）

        参考: behavior-model.md#1.3 Token鉴权验证
        """
        # [R_TOKEN_002] Token 有效性校验
        if not token:
            raise TokenExpiredException("请先登录")

        # 尝试解密验证 Token 格式
        try:
            sm4_decrypt(token)
        except Exception:
            raise TokenExpiredException("Token无效")

        # 计算 Token 哈希
        token_hash = generate_token_hash(token)
        token_key = get_token_key(token_hash)

        # 从 Redis 获取 Token 信息
        token_data = await redis_get(token_key)

        if not token_data:
            raise TokenExpiredException("登录已过期，请重新登录")

        token_info = json.loads(token_data)
        login_time_str = token_info.get("login_time")

        if not login_time_str:
            raise TokenExpiredException("Token数据损坏")

        login_time = datetime.fromisoformat(login_time_str)
        # 确保 login_time 是 aware datetime
        if login_time.tzinfo is None:
            login_time = timezone.make_aware(login_time)

        now = timezone.now()

        # [R_TOKEN_003] 检查24小时绝对过期
        elapsed = (now - login_time).total_seconds()
        if elapsed >= settings.AUTH_TOKEN_ABSOLUTE_TTL:  # 24小时
            await redis_delete(token_key)
            raise TokenExpiredException("登录已超过24小时，请重新登录")

        # [R_TOKEN_003] 刷新TTL（1小时无操作过期，但不超过24小时边界）
        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        await redis_expire(token_key, ttl)

        # 更新最后活跃时间
        token_info["last_active_time"] = now.isoformat()

        return token_info

    def _unauthorized_response(self, message: str) -> JsonResponse:
        """返回 401 未授权响应"""
        return JsonResponse(
            {
                "code": "UNAUTHORIZED",
                "message": message,
                "data": None,
            },
            status=401,
        )


class AsyncTokenAuthMiddleware:
    """
    异步 Token 认证中间件

    用于 ASGI 应用（如 Django Channels）
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        # 检查是否为公开路径
        if self._is_public_path(request.path):
            response = self.get_response(request)
            if asyncio.iscoroutine(response):
                response = await response
            return response

        # 从 httpOnly Cookie 获取 Token
        token = request.COOKIES.get(TOKEN_COOKIE_NAME)

        if not token:
            return self._unauthorized_response("请先登录")

        # 验证 Token
        try:
            user_info = await self._verify_token(token)
            # 将用户信息附加到 request
            request.user_id = user_info["user_id"]
            request.username = user_info["username"]
            request.token_hash = generate_token_hash(token)
        except TokenExpiredException as e:
            return self._unauthorized_response(str(e))
        except Exception as e:
            logger.warning(f"Token verification failed: {e}")
            return self._unauthorized_response("认证失败")

        response = self.get_response(request)
        if asyncio.iscoroutine(response):
            response = await response
        return response

    def _is_public_path(self, path: str) -> bool:
        """检查是否为公开路径"""
        for public_path in PUBLIC_PATHS:
            if path.startswith(public_path):
                return True
        return False

    async def _verify_token(self, token: str) -> dict:
        """异步验证 Token"""
        # [R_TOKEN_002] Token 有效性校验
        if not token:
            raise TokenExpiredException("请先登录")

        try:
            sm4_decrypt(token)
        except Exception:
            raise TokenExpiredException("Token无效")

        token_hash = generate_token_hash(token)
        token_key = get_token_key(token_hash)

        token_data = await redis_get(token_key)

        if not token_data:
            raise TokenExpiredException("登录已过期，请重新登录")

        token_info = json.loads(token_data)
        login_time_str = token_info.get("login_time")

        if not login_time_str:
            raise TokenExpiredException("Token数据损坏")

        login_time = datetime.fromisoformat(login_time_str)
        if login_time.tzinfo is None:
            login_time = timezone.make_aware(login_time)

        now = timezone.now()

        # [R_TOKEN_003] 检查24小时绝对过期
        elapsed = (now - login_time).total_seconds()
        if elapsed >= settings.AUTH_TOKEN_ABSOLUTE_TTL:
            await redis_delete(token_key)
            raise TokenExpiredException("登录已超过24小时，请重新登录")

        # [R_TOKEN_003] 刷新TTL
        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        await redis_expire(token_key, ttl)

        return token_info

    def _unauthorized_response(self, message: str) -> JsonResponse:
        """返回 401 未授权响应"""
        return JsonResponse(
            {
                "code": "UNAUTHORIZED",
                "message": message,
                "data": None,
            },
            status=401,
        )


def set_token_cookie(response: HttpResponse, token: str, max_age: int = 3600) -> HttpResponse:
    """
    设置 Token Cookie

    参考: constitution.md#4.1 Token存储httpOnly Cookie

    Args:
        response: HTTP 响应对象
        token: Token 字符串
        max_age: Cookie 有效期（秒），默认1小时

    Returns:
        设置了 Cookie 的响应对象
    """
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,  # 禁止 JavaScript 访问
        secure=not settings.DEBUG,  # 生产环境只允许 HTTPS
        samesite="Lax",  # 防止 CSRF
        path="/",
    )
    return response


def clear_token_cookie(response: HttpResponse) -> HttpResponse:
    """
    清除 Token Cookie

    用于登出操作

    Args:
        response: HTTP 响应对象

    Returns:
        清除了 Cookie 的响应对象
    """
    response.delete_cookie(
        key=TOKEN_COOKIE_NAME,
        path="/",
    )
    return response
