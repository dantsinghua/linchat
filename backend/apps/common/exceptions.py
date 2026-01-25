"""
自定义异常类和异常处理器

参考: constitution.md#4.3 大模型异常处理
参考: behavior-model.md#1.2 用户登录
"""
from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


# ============ 认证异常 ============

class AuthException(Exception):
    """认证异常基类"""
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "认证失败"
    error_code = "AUTH_ERROR"

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class AuthFailedException(AuthException):
    """认证失败"""
    default_message = "用户名或密码错误"
    error_code = "AUTH_FAILED"


class TokenExpiredException(AuthException):
    """Token已过期"""
    default_message = "登录已过期，请重新登录"
    error_code = "TOKEN_EXPIRED"


class AccountLockedException(AuthException):
    """账户已锁定"""
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "账户已锁定，请稍后再试"
    error_code = "ACCOUNT_LOCKED"

    def __init__(self, message: str | None = None, remaining_seconds: int = 0):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


class CaptchaInvalidException(AuthException):
    """验证码无效"""
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "验证码错误或已过期"
    error_code = "CAPTCHA_INVALID"


# ============ LLM 异常 (宪法4.3) ============

class LLMException(Exception):
    """LLM异常基类"""
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "AI服务异常"
    error_code = "LLM_ERROR"
    should_retry = False
    max_retries = 0

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class LLMConnectionError(LLMException):
    """LLM连接失败 - 重试3次"""
    default_message = "AI服务暂时无法连接，请稍后重试"
    error_code = "LLM_CONNECTION_ERROR"
    should_retry = True
    max_retries = 3


class LLMTimeoutError(LLMException):
    """LLM超时 - 重试3次"""
    default_message = "AI响应超时，请稍后重试"
    error_code = "LLM_TIMEOUT"
    should_retry = True
    max_retries = 3


class LLMRateLimitError(LLMException):
    """LLM频率限制 - 不重试，返回等待时间"""
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_message = "请求过于频繁，请稍后重试"
    error_code = "LLM_RATE_LIMIT"
    should_retry = False

    def __init__(self, message: str | None = None, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class LLMContentFilterError(LLMException):
    """LLM内容过滤 - 不重试，允许用户修改"""
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "消息包含敏感内容，请修改后重试"
    error_code = "LLM_CONTENT_FILTER"
    should_retry = False


class LLMInvalidResponseError(LLMException):
    """LLM无效响应 - 重试3次"""
    default_message = "AI返回了无效响应，请重试"
    error_code = "LLM_INVALID_RESPONSE"
    should_retry = True
    max_retries = 3


class LLMQuotaExceededError(LLMException):
    """LLM配额用尽 - 不重试"""
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_message = "服务配额用尽，请联系管理员"
    error_code = "LLM_QUOTA_EXCEEDED"
    should_retry = False


# ============ 业务异常 ============

class BusinessException(Exception):
    """业务异常基类"""
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "业务处理失败"
    error_code = "BUSINESS_ERROR"

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class MessageTooLongException(BusinessException):
    """消息过长"""
    default_message = "消息长度超过限制"
    error_code = "MESSAGE_TOO_LONG"


class EmptyMessageException(BusinessException):
    """空消息"""
    default_message = "消息不能为空"
    error_code = "EMPTY_MESSAGE"


# ============ 异常处理器 ============

def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """
    自定义异常处理器

    统一响应格式: {"code": "ERROR_CODE", "message": "错误信息", "data": null}
    """
    # 先调用 DRF 默认的异常处理
    response = exception_handler(exc, context)

    # 处理自定义异常
    if isinstance(exc, (AuthException, LLMException, BusinessException)):
        data = {
            "code": exc.error_code,
            "message": exc.message,
            "data": None,
        }

        # 添加额外信息
        if isinstance(exc, AccountLockedException) and exc.remaining_seconds > 0:
            data["remaining_seconds"] = exc.remaining_seconds
        elif isinstance(exc, LLMRateLimitError):
            data["retry_after"] = exc.retry_after

        return Response(data, status=exc.status_code)

    # 处理 DRF 默认异常
    if response is not None:
        # 统一格式化 DRF 异常响应
        if isinstance(response.data, dict):
            message = response.data.get("detail", str(response.data))
        else:
            message = str(response.data)

        response.data = {
            "code": "ERROR",
            "message": message,
            "data": None,
        }

    return response
