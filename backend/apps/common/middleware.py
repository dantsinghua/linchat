"""认证中间件

实现 R_TOKEN_003 双重过期规则：
- 无操作过期: 1小时无操作自动过期，有操作时刷新TTL
- 绝对过期: 登录后24小时强制失效
"""

import json
import logging
from datetime import datetime
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from apps.common.exceptions import TokenExpiredException
from apps.users.crypto import generate_token_hash, sm4_decrypt
from core.redis import (get_token_key, sync_redis_delete, sync_redis_expire,
                        sync_redis_get)

logger = logging.getLogger(__name__)

PUBLIC_PATHS = [
    "/api/v1/auth/captcha",
    "/api/v1/auth/login",
    "/api/v1/health/",
    "/admin/",
    "/static/",
]

TOKEN_COOKIE_NAME = "linchat_token"


class TokenAuthMiddleware:
    """Token 认证中间件（同步）

    Django 自动在线程池中运行同步中间件以支持异步视图。
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if any(request.path.startswith(p) for p in PUBLIC_PATHS):
            return self.get_response(request)

        token = request.COOKIES.get(TOKEN_COOKIE_NAME)
        if not token:
            return self._unauthorized_response("请先登录")

        try:
            user_info = self._verify_token_sync(token)
            request.user_id = user_info["user_id"]
            request.username = user_info["username"]
            request.user_type = user_info.get("user_type", "user")
            request.token_hash = generate_token_hash(token)
        except TokenExpiredException as e:
            return self._unauthorized_response(str(e))
        except Exception as e:
            logger.warning(f"Token verification failed: {e}")
            return self._unauthorized_response("认证失败")

        return self.get_response(request)

    def _verify_token_sync(self, token: str) -> dict:
        """同步验证 Token"""
        if not token:
            raise TokenExpiredException("请先登录")

        try:
            sm4_decrypt(token)
        except Exception:
            raise TokenExpiredException("Token无效")

        token_hash = generate_token_hash(token)
        token_key = get_token_key(token_hash)
        token_data = sync_redis_get(token_key)

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
        elapsed = (now - login_time).total_seconds()

        if elapsed >= settings.AUTH_TOKEN_ABSOLUTE_TTL:
            sync_redis_delete(token_key)
            raise TokenExpiredException("登录已超过24小时，请重新登录")

        remaining_absolute = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        ttl = min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining_absolute))
        sync_redis_expire(token_key, ttl)

        token_info["last_active_time"] = now.isoformat()
        return token_info

    def _unauthorized_response(self, message: str) -> JsonResponse:
        return JsonResponse(
            {"code": "UNAUTHORIZED", "message": message, "data": None},
            status=401,
        )


def set_token_cookie(
    response: HttpResponse, token: str, max_age: int = 3600
) -> HttpResponse:
    """设置 Token Cookie（httpOnly）"""
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
    )
    return response


def clear_token_cookie(response: HttpResponse) -> HttpResponse:
    """清除 Token Cookie"""
    response.delete_cookie(key=TOKEN_COOKIE_NAME, path="/")
    return response
