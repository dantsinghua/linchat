"""
用户认证视图

参考:
- process-model.md#一、用户登录流程（P_AUTH_001）
- behavior-model.md#1.1 获取验证码（B_AUTH_001）
- behavior-model.md#1.2 用户登录（B_AUTH_002）

使用 Django 4.1+ 异步视图，无需手动管理事件循环
"""
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status

from apps.common.exceptions import (
    AccountLockedException,
    AuthFailedException,
    CaptchaInvalidException,
    UserDisabledException,
)
from apps.common.middleware import (
    TOKEN_COOKIE_NAME,
    clear_token_cookie,
    set_token_cookie,
)
from apps.common.responses import api_response, error_response
from apps.users.serializers import (
    CaptchaResponseSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    UserInfoSerializer,
)
from apps.users.services import AuthService, CaptchaService

logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> str:
    """获取客户端 IP"""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


@method_decorator(csrf_exempt, name="dispatch")
class CaptchaView(View):
    """
    验证码视图

    GET /api/v1/auth/captcha - 获取验证码
    """

    async def get(self, request: HttpRequest) -> JsonResponse:
        """
        获取验证码

        参考: behavior-model.md#1.1 获取验证码（B_AUTH_001）
        规则: R_CAPTCHA_001 - 验证码有效期2分钟
        """
        try:
            result = await CaptchaService.generate()

            serializer = CaptchaResponseSerializer(
                {
                    "captcha_id": result.captcha_id,
                    "captcha_image": result.captcha_image,
                }
            )

            return api_response(data=serializer.data)

        except Exception as e:
            logger.exception("Failed to generate captcha")
            return error_response(
                message="验证码生成失败",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    """
    登录视图

    POST /api/v1/auth/login - 用户登录
    """

    async def post(self, request: HttpRequest) -> JsonResponse:
        """
        用户登录

        参考: process-model.md#一、用户登录流程（P_AUTH_001）
        参考: behavior-model.md#1.2 用户登录（B_AUTH_002）
        """
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return error_response(
                message="请求格式错误",
                code="INVALID_REQUEST",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # 验证请求数据
        serializer = LoginRequestSerializer(data=body)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = next(iter(errors.values()))[0]
            return error_response(
                message=str(first_error),
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data
        client_ip = get_client_ip(request)

        try:
            result = await AuthService.login(
                username=validated_data["username"],
                encrypted_password=validated_data["password"],
                captcha_id=validated_data["captcha_id"],
                captcha_code=validated_data["captcha_code"],
                client_ip=client_ip,
            )

            # 构建响应
            response_serializer = LoginResponseSerializer(
                {
                    "user_id": result.user_id,
                    "username": result.username,
                    "expire_time": result.expire_time,
                }
            )

            response = api_response(
                data=response_serializer.data,
                message="登录成功",
            )

            # 设置 httpOnly Cookie
            # 参考: constitution.md#4.1 Token存储httpOnly Cookie
            set_token_cookie(response, result.token)

            logger.info(f"User {result.username} logged in from {client_ip}")
            return response

        except CaptchaInvalidException as e:
            return error_response(
                message=str(e),
                code="CAPTCHA_INVALID",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        except AccountLockedException as e:
            return error_response(
                message=str(e),
                code="ACCOUNT_LOCKED",
                status_code=status.HTTP_403_FORBIDDEN,
                extra={"remaining_seconds": e.remaining_seconds},
            )

        except AuthFailedException as e:
            return error_response(
                message=str(e),
                code="AUTH_FAILED",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        except UserDisabledException as e:
            return error_response(
                message=str(e),
                code="USER_DISABLED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        except Exception as e:
            logger.exception("Login failed")
            return error_response(
                message="登录失败，请稍后重试",
                code="LOGIN_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(View):
    """
    登出视图

    POST /api/v1/auth/logout - 用户登出
    """

    async def post(self, request: HttpRequest) -> JsonResponse:
        """用户登出"""
        try:
            user_id = getattr(request, "user_id", None)
            token_hash = getattr(request, "token_hash", None)

            if user_id and token_hash:
                await AuthService.logout(user_id, token_hash)

            # 清除 Cookie
            response = api_response(message="登出成功")
            clear_token_cookie(response)

            return response

        except Exception as e:
            logger.exception("Logout failed")
            # 即使出错也清除 Cookie
            response = api_response(message="登出成功")
            clear_token_cookie(response)
            return response


@method_decorator(csrf_exempt, name="dispatch")
class MeView(View):
    """
    当前用户信息视图

    GET /api/v1/auth/me - 获取当前用户信息
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """获取当前用户信息"""
        user_id = getattr(request, "user_id", None)
        username = getattr(request, "username", None)

        if not user_id:
            return error_response(
                message="未登录",
                code="UNAUTHORIZED",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = UserInfoSerializer(
            {
                "user_id": user_id,
                "username": username,
            }
        )

        return api_response(data=serializer.data)
