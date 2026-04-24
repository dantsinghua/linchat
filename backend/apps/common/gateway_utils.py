import functools
import logging
import uuid
from typing import Any, Callable, Optional

import httpx
from django.conf import settings
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt, wait_exponential,
)

from apps.common.exceptions import (
    LLMConnectionError, LLMContentFilterError, LLMRateLimitError, LLMTimeoutError,
)

logger = logging.getLogger(__name__)


def build_gateway_headers(request_id: Optional[str] = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = getattr(settings, "LLM_GATEWAY_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if not request_id:
        # batch-05：优先继承 HTTP 链路的 trace_id（32-hex），确保 gateway 子请求
        # 与父请求在日志/Langfuse 中能聚合；兜底时也用 hex 格式统一
        from apps.common import get_trace_id
        request_id = get_trace_id() or uuid.uuid4().hex
    headers["X-Request-ID"] = request_id
    return headers


def get_gateway_url() -> str:
    url = getattr(settings, "LLM_GATEWAY_URL", "")
    if not url:
        raise LLMConnectionError("未配置 LLM_GATEWAY_URL")
    return url


class GatewayError:
    def __init__(self, code: str, message: str, details: Optional[dict] = None, http_status: int = 500):
        self.code = code; self.message = message
        self.details = details or {}; self.http_status = http_status


def parse_gateway_error(response: httpx.Response) -> GatewayError:
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
    return GatewayError(code=code, message=message, details=details, http_status=response.status_code)


def map_httpx_exception(e: Exception) -> Exception:
    if isinstance(e, httpx.TimeoutException):
        return LLMTimeoutError(f"Gateway 请求超时: {e}")
    if isinstance(e, httpx.ConnectError):
        return LLMConnectionError(f"Gateway 连接失败: {e}")
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 429:
            retry_after = int(e.response.headers.get("Retry-After", "60"))
            return LLMRateLimitError(f"Gateway 频率限制: {e}", retry_after=retry_after)
        if e.response.status_code == 400:
            body = e.response.text
            if "content_filter" in body or "content_control" in body:
                return LLMContentFilterError(f"内容审核拦截: {body[:200]}")
        return LLMConnectionError(f"Gateway HTTP {e.response.status_code}: {e}")
    if isinstance(e, (LLMConnectionError, LLMTimeoutError, LLMRateLimitError, LLMContentFilterError)):
        return e
    return LLMConnectionError(f"Gateway 请求异常: {e}")


def gateway_retry(max_retries: int = 3, retry_on: tuple = (LLMConnectionError, LLMTimeoutError)) -> Callable:
    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_retries + 1),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(retry_on), reraise=True,
        )
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)
        return wrapper
    return decorator


_langfuse_client = None


def _get_langfuse():
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    from langfuse import Langfuse
    public_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    secret_key = getattr(settings, "LANGFUSE_SECRET_KEY", "")
    host = getattr(settings, "LANGFUSE_HOST", "")
    if not (public_key and secret_key):
        return None
    _langfuse_client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    return _langfuse_client


def record_gateway_span(
    request_type: str, model: str, duration: float, status_code: int,
    request_id: Optional[str] = None, error: Optional[str] = None,
) -> None:
    try:
        langfuse = _get_langfuse()
        if not langfuse:
            return
        from apps.common import get_trace_id
        metadata: dict[str, Any] = {
            "model": model, "request_type": request_type,
            "duration": round(duration, 3), "status_code": status_code,
            "request_id": request_id or "",
            "trace_id": get_trace_id() or request_id or "",
        }
        if error: metadata["error"] = error
        span = langfuse.start_observation(
            name=f"gateway_{request_type}", metadata=metadata,
            level="ERROR" if error else "DEFAULT",
        )
        span.end()
        # 不再同步 flush，由 BatchSpanProcessor 定时批量导出
    except Exception as e:
        logger.warning("Langfuse span 记录失败 (%s): %s", request_type, e)
