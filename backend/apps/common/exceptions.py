from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class AppException(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "操作失败"
    error_code = "ERROR"

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class AuthException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "认证失败"; error_code = "AUTH_ERROR"


class AuthFailedException(AuthException):
    default_message = "用户名或密码错误"; error_code = "AUTH_FAILED"


class TokenExpiredException(AuthException):
    default_message = "登录已过期，请重新登录"; error_code = "TOKEN_EXPIRED"


class AccountLockedException(AuthException):
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "账户已锁定，请稍后再试"; error_code = "ACCOUNT_LOCKED"

    def __init__(self, message: str | None = None, remaining_seconds: int = 0):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


class CaptchaInvalidException(AuthException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "验证码错误或已过期"; error_code = "CAPTCHA_INVALID"


class UserDisabledException(AuthException):
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "账户已被禁用"; error_code = "USER_DISABLED"


class LLMException(AppException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "AI服务异常"; error_code = "LLM_ERROR"
    should_retry = False; max_retries = 0


class LLMConnectionError(LLMException):
    default_message = "AI服务暂时无法连接，请稍后重试"; error_code = "LLM_CONNECTION_ERROR"
    should_retry = True; max_retries = 3


class LLMTimeoutError(LLMException):
    default_message = "AI响应超时，请稍后重试"; error_code = "LLM_TIMEOUT"
    should_retry = True; max_retries = 3


class LLMRateLimitError(LLMException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_message = "请求过于频繁，请稍后重试"; error_code = "LLM_RATE_LIMIT"

    def __init__(self, message: str | None = None, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class LLMContentFilterError(LLMException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "消息包含敏感内容，请修改后重试"; error_code = "LLM_CONTENT_FILTER"


class LLMInvalidResponseError(LLMException):
    default_message = "AI返回了无效响应，请重试"; error_code = "LLM_INVALID_RESPONSE"
    should_retry = True; max_retries = 3


class LLMQuotaExceededError(LLMException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_message = "服务配额用尽，请联系管理员"; error_code = "LLM_QUOTA_EXCEEDED"


class LLMContextLengthError(LLMException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "对话历史过长，请缩短语音输入或重新开始会话"; error_code = "LLM_CONTEXT_LENGTH"


class ExternalServiceError(AppException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_message = "外部服务异常"; error_code = "EXTERNAL_SERVICE_ERROR"


_LLM_ERROR_MAP: list[tuple[list[str], type[LLMException]]] = [
    (["connection", "connect", "network", "unreachable"], LLMConnectionError),
    (["timeout", "timed out"], LLMTimeoutError),
    (["rate limit", "too many requests", "429"], LLMRateLimitError),
    (["content filter", "content policy", "moderation"], LLMContentFilterError),
    (["quota", "insufficient", "billing"], LLMQuotaExceededError),
]


def map_llm_exception(e: Exception) -> LLMException:
    # isinstance 优先：httpx 异常的 str() 可能为空，关键词匹配会漏掉
    try:
        import httpx
        if isinstance(e, httpx.TimeoutException):
            return LLMTimeoutError()
        if isinstance(e, httpx.ConnectError):
            return LLMConnectionError()
    except ImportError:
        pass
    error_str = str(e).lower()
    for keywords, exc_class in _LLM_ERROR_MAP:
        if any(kw in error_str for kw in keywords):
            return exc_class()
    return LLMInvalidResponseError(str(e))


class BusinessException(AppException):
    default_message = "业务处理失败"; error_code = "BUSINESS_ERROR"


class MessageTooLongException(BusinessException):
    default_message = "消息长度超过限制"; error_code = "MESSAGE_TOO_LONG"


class EmptyMessageException(BusinessException):
    default_message = "消息不能为空"; error_code = "EMPTY_MESSAGE"


def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = exception_handler(exc, context)
    if isinstance(exc, AppException):
        data: dict[str, Any] = {"code": exc.error_code, "message": exc.message, "data": None}
        if isinstance(exc, AccountLockedException) and exc.remaining_seconds > 0:
            data["remaining_seconds"] = exc.remaining_seconds
        elif isinstance(exc, LLMRateLimitError):
            data["retry_after"] = exc.retry_after
        return Response(data, status=exc.status_code)
    if response is not None:
        message = response.data.get("detail", str(response.data)) if isinstance(response.data, dict) else str(response.data)
        response.data = {"code": "ERROR", "message": message, "data": None}
    return response
