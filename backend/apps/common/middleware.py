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
    get_token_key, sync_redis_delete, sync_redis_expire,
    sync_redis_get, sync_redis_setex_json,
)

logger = logging.getLogger(__name__)
PUBLIC_PATHS = ["/api/v1/auth/captcha", "/api/v1/auth/login", "/api/v1/health/", "/api/v1/internal/", "/admin/", "/static/"]
TOKEN_COOKIE_NAME = "linchat_token"


class TokenAuthMiddleware:
    """Token 认证中间件（同步）"""

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

        from apps.users.models import SysUser
        member_type = user_info.get("member_type")
        user_obj = None
        if not member_type:
            try:
                user_obj = SysUser.objects.get(user_id=request.user_id, status=1)
                member_type = user_obj.member_type
            except SysUser.DoesNotExist:
                member_type = "member"
            user_info["member_type"] = member_type
            token_key = get_token_key(request.token_hash)
            remaining_ttl = self._get_key_ttl_sync(token_key)
            if remaining_ttl and remaining_ttl > 0:
                sync_redis_setex_json(token_key, remaining_ttl, user_info)
        if member_type == "guest":
            try:
                guest = user_obj or SysUser.objects.get(user_id=request.user_id, status=1)
                if guest.is_guest_expired():
                    logger.warning("过期访客 %s 尝试使用存量 Token 访问", request.user_id)
                    return self._unauthorized_response("账号已过期")
            except SysUser.DoesNotExist:
                pass
        request.member_type = member_type
        error = self._resolve_target_user(request, member_type)
        return error if error else self.get_response(request)

    def _resolve_target_user(self, request: HttpRequest, member_type: str) -> HttpResponse | None:
        header = request.META.get("HTTP_X_TARGET_USER_ID")
        if not (header and member_type == "member"):
            request.target_user_id = request.user_id
            return None
        from apps.users.models import SysUser
        try:
            target_uid = int(header)
        except (ValueError, TypeError):
            return self._bad_request_response("TARGET_USER_INVALID", "X-Target-User-Id 格式无效")
        if target_uid == request.user_id:
            request.target_user_id = request.user_id
            return None
        try:
            target_user = SysUser.objects.get(user_id=target_uid, status=1)
        except SysUser.DoesNotExist:
            logger.warning("用户 %s 切换目标用户 %s 不存在或已禁用", request.user_id, target_uid)
            return self._bad_request_response("TARGET_USER_INVALID", "目标用户不存在或已禁用")
        if target_user.is_guest_expired():
            logger.warning("用户 %s 切换目标访客 %s 已过期", request.user_id, target_uid)
            return self._bad_request_response("TARGET_USER_INVALID", "目标用户已过期")
        logger.info("用户 %s 切换到目标用户 %s", request.user_id, target_uid)
        request.target_user_id = target_uid
        return None

    def _verify_token_sync(self, token: str) -> dict:
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
        remaining = settings.AUTH_TOKEN_ABSOLUTE_TTL - elapsed
        sync_redis_expire(token_key, min(settings.AUTH_TOKEN_IDLE_TTL, int(remaining)))
        token_info["last_active_time"] = now.isoformat()
        return token_info

    def _get_key_ttl_sync(self, key: str) -> int | None:
        from core.redis import SyncRedisClient
        ttl = SyncRedisClient.get_client().ttl(key)
        return ttl if ttl > 0 else None

    def _bad_request_response(self, code: str, message: str) -> JsonResponse:
        return JsonResponse({"code": code, "message": message, "data": None}, status=400)

    def _unauthorized_response(self, message: str) -> JsonResponse:
        resp = JsonResponse({"code": "UNAUTHORIZED", "message": message, "data": None}, status=401)
        clear_token_cookie(resp)
        return resp


def set_token_cookie(response: HttpResponse, token: str, max_age: int = 3600) -> HttpResponse:
    response.set_cookie(
        key=TOKEN_COOKIE_NAME, value=token, max_age=max_age,
        httponly=True, secure=not settings.DEBUG, samesite="Lax", path="/",
    )
    return response


def clear_token_cookie(response: HttpResponse) -> HttpResponse:
    response.delete_cookie(key=TOKEN_COOKIE_NAME, path="/")
    return response
