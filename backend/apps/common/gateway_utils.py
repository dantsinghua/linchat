"""
Gateway 共享工具模块 (T067)

提供 Gateway API 调用的通用工具函数：
- 请求头构建（Authorization + X-Request-ID）
- Gateway 错误响应解析
- 重试装饰器（基于 tenacity）
- Langfuse span 记录

参考:
- plan.md Constitution Check 4.3
- docs/upstream-integration-guide.md
"""

import functools
import logging
import time
import uuid
from typing import Any, Callable, Optional

import httpx
from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from apps.common.exceptions import (
    LLMConnectionError,
    LLMContentFilterError,
    LLMRateLimitError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


def build_gateway_headers(request_id: Optional[str] = None) -> dict[str, str]:
    """构建 Gateway 请求头

    注入 Authorization 和 X-Request-ID 头，
    供 InferenceService/DocumentParseService/TTSService 共用。

    Args:
        request_id: 请求 ID（链路追踪），不提供则自动生成

    Returns:
        请求头字典
    """
    headers: dict[str, str] = {}

    api_key = getattr(settings, "LLM_GATEWAY_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if not request_id:
        request_id = str(uuid.uuid4())
    headers["X-Request-ID"] = request_id

    return headers


def get_gateway_url() -> str:
    """获取 Gateway 基础地址

    Returns:
        Gateway URL

    Raises:
        LLMConnectionError: 未配置 Gateway URL
    """
    url = getattr(settings, "LLM_GATEWAY_URL", "")
    if not url:
        raise LLMConnectionError("未配置 LLM_GATEWAY_URL")
    return url


class GatewayError:
    """Gateway 错误响应解析结果"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[dict] = None,
        http_status: int = 500,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.http_status = http_status


def parse_gateway_error(response: httpx.Response) -> GatewayError:
    """解析 Gateway 错误响应

    将 Gateway 返回的 {"error": {"code": "Exxxx", "message": "...", "details": {...}}}
    格式解析为 GatewayError 对象。

    Args:
        response: httpx 响应对象

    Returns:
        GatewayError 解析结果
    """
    try:
        body = response.json()
        error_info = body.get("error", {})
        code = error_info.get("code", f"HTTP_{response.status_code}")
        message = error_info.get("message", f"Gateway 返回 {response.status_code}")
        details = error_info.get("details", {})
    except Exception:
        code = f"HTTP_{response.status_code}"
        message = f"Gateway 返回 {response.status_code}"
        details = {}

    return GatewayError(
        code=code,
        message=message,
        details=details,
        http_status=response.status_code,
    )


def map_httpx_exception(e: Exception) -> Exception:
    """将 httpx 异常映射为 LLM 标准异常

    Args:
        e: httpx 原始异常

    Returns:
        映射后的 LLM 异常
    """
    if isinstance(e, httpx.TimeoutException):
        return LLMTimeoutError(f"Gateway 请求超时: {e}")
    elif isinstance(e, httpx.ConnectError):
        return LLMConnectionError(f"Gateway 连接失败: {e}")
    elif isinstance(e, (LLMConnectionError, LLMTimeoutError)):
        return e
    else:
        return LLMConnectionError(f"Gateway 请求异常: {e}")


def gateway_retry(
    max_retries: int = 3,
    retry_on: tuple = (LLMConnectionError, LLMTimeoutError),
) -> Callable:
    """Gateway 调用重试装饰器

    基于 tenacity 实现指数退避重试。
    LLMRateLimitError/LLMContentFilterError 等不重试，直接向上抛出。

    Args:
        max_retries: 最大重试次数
        retry_on: 触发重试的异常类型

    Returns:
        装饰器函数
    """

    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_retries + 1),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(retry_on),
            reraise=True,
        )
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def record_gateway_span(
    request_type: str,
    model: str,
    duration: float,
    status_code: int,
    request_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """记录 Gateway 调用 Langfuse span

    Args:
        request_type: 请求类型 (tts/document_parse/inference_cancel 等)
        model: 使用的模型名称
        duration: 请求耗时（秒）
        status_code: HTTP 状态码
        request_id: 请求 ID
        error: 错误信息（可选）
    """
    try:
        from langfuse import Langfuse

        public_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", "")
        secret_key = getattr(settings, "LANGFUSE_SECRET_KEY", "")
        host = getattr(settings, "LANGFUSE_HOST", "")

        if not (public_key and secret_key):
            return

        langfuse = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )

        trace = langfuse.trace(
            name=f"gateway_{request_type}",
            metadata={
                "request_type": request_type,
                "model": model,
                "request_id": request_id or "",
            },
        )

        span_data: dict[str, Any] = {
            "name": request_type,
            "metadata": {
                "model": model,
                "request_type": request_type,
                "duration": round(duration, 3),
                "status_code": status_code,
                "request_id": request_id or "",
            },
        }
        if error:
            span_data["metadata"]["error"] = error
            span_data["level"] = "ERROR"

        trace.span(**span_data)
        langfuse.flush()

    except Exception as e:
        logger.debug(f"Langfuse span 记录失败: {e}")
