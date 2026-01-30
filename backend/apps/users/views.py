"""用户认证视图 — 验证码 / 登录 / 登出 / 当前用户"""
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status

from apps.common.exceptions import AuthException
from apps.common.middleware import clear_token_cookie, set_token_cookie
from apps.common.responses import api_response, error_response
from apps.users.serializers import LoginRequestSerializer
from apps.users.services import AuthService, CaptchaService

logger = logging.getLogger(__name__)


def _get_client_ip(request: HttpRequest) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "unknown")


def _handle_auth_exception(e: AuthException) -> JsonResponse:
    """AuthException 统一转 JsonResponse"""
    extra = {}
    if hasattr(e, "remaining_seconds") and e.remaining_seconds:
        extra["remaining_seconds"] = e.remaining_seconds
    return error_response(
        message=str(e), code=e.error_code, status_code=e.status_code, extra=extra or None
    )


@method_decorator(csrf_exempt, name="dispatch")
class CaptchaView(View):
    """GET /api/v1/auth/captcha"""

    async def get(self, request: HttpRequest) -> JsonResponse:
        try:
            result = await CaptchaService.generate()
            return api_response(data=result)
        except Exception:
            logger.exception("Failed to generate captcha")
            return error_response(
                message="验证码生成失败", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    """POST /api/v1/auth/login"""

    async def post(self, request: HttpRequest) -> JsonResponse:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return error_response(
                message="请求格式错误", code="INVALID_REQUEST",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        serializer = LoginRequestSerializer(data=body)
        if not serializer.is_valid():
            first_error = next(iter(serializer.errors.values()))[0]
            return error_response(
                message=str(first_error), code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = await AuthService.login(
                username=serializer.validated_data["username"],
                encrypted_password=serializer.validated_data["password"],
                captcha_id=serializer.validated_data["captcha_id"],
                captcha_code=serializer.validated_data["captcha_code"],
                client_ip=_get_client_ip(request),
            )
            resp = api_response(
                data={
                    "user_id": result["user_id"],
                    "username": result["username"],
                    "expire_time": result["expire_time"].isoformat(),
                },
                message="登录成功",
            )
            set_token_cookie(resp, result["token"])
            logger.info(f"User {result['username']} logged in from {_get_client_ip(request)}")
            return resp

        except AuthException as e:
            return _handle_auth_exception(e)
        except Exception:
            logger.exception("Login failed")
            return error_response(
                message="登录失败，请稍后重试", code="LOGIN_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(View):
    """POST /api/v1/auth/logout"""

    async def post(self, request: HttpRequest) -> JsonResponse:
        user_id = getattr(request, "user_id", None)
        token_hash = getattr(request, "token_hash", None)
        try:
            if user_id and token_hash:
                await AuthService.logout(user_id, token_hash)
        except Exception:
            logger.exception("Logout failed")

        resp = api_response(message="登出成功")
        clear_token_cookie(resp)
        return resp


@method_decorator(csrf_exempt, name="dispatch")
class MeView(View):
    """GET /api/v1/auth/me"""

    def get(self, request: HttpRequest) -> JsonResponse:
        user_id = getattr(request, "user_id", None)
        if not user_id:
            return error_response(
                message="未登录", code="UNAUTHORIZED",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return api_response(data={
            "user_id": user_id,
            "username": getattr(request, "username", None),
            "type": getattr(request, "user_type", "user"),
        })
