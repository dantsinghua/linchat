"""
Gateway 工具模块单元测试 (T067)

覆盖:
- build_gateway_headers: 生成 Authorization + X-Request-ID
- get_gateway_url: 获取 Gateway URL
- parse_gateway_error: 解析 Gateway 错误响应
- map_httpx_exception: httpx 异常映射
- gateway_retry: 重试装饰器
- record_gateway_span: Langfuse span 记录
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.common.gateway_utils import (
    GatewayError,
    build_gateway_headers,
    gateway_retry,
    get_gateway_url,
    map_httpx_exception,
    parse_gateway_error,
    record_gateway_span,
)
from apps.common.exceptions import LLMConnectionError, LLMTimeoutError


class TestBuildGatewayHeaders:
    """请求头构建测试"""

    @patch("apps.common.gateway_utils.settings")
    def test_with_api_key(self, mock_settings):
        """有 API Key 时包含 Authorization 头"""
        mock_settings.LLM_GATEWAY_API_KEY = "test-api-key"

        headers = build_gateway_headers(request_id="req-123")

        assert headers["Authorization"] == "Bearer test-api-key"
        assert headers["X-Request-ID"] == "req-123"

    @patch("apps.common.gateway_utils.settings")
    def test_without_api_key(self, mock_settings):
        """无 API Key 时不包含 Authorization 头"""
        mock_settings.LLM_GATEWAY_API_KEY = ""

        headers = build_gateway_headers(request_id="req-123")

        assert "Authorization" not in headers
        assert headers["X-Request-ID"] == "req-123"

    @patch("apps.common.gateway_utils.settings")
    def test_auto_generate_request_id(self, mock_settings):
        """不提供 request_id 时自动生成"""
        mock_settings.LLM_GATEWAY_API_KEY = ""

        headers = build_gateway_headers()

        assert "X-Request-ID" in headers
        assert len(headers["X-Request-ID"]) > 0


class TestGetGatewayUrl:
    """Gateway URL 获取测试"""

    @patch("apps.common.gateway_utils.settings")
    def test_configured(self, mock_settings):
        """已配置时返回 URL"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8000"

        assert get_gateway_url() == "http://gateway:8000"

    @patch("apps.common.gateway_utils.settings")
    def test_not_configured(self, mock_settings):
        """未配置时抛出 LLMConnectionError"""
        mock_settings.LLM_GATEWAY_URL = ""

        with pytest.raises(LLMConnectionError):
            get_gateway_url()


class TestParseGatewayError:
    """Gateway 错误解析测试"""

    def test_parse_standard_error(self):
        """解析标准 Gateway 错误"""
        response = MagicMock()
        response.status_code = 404
        response.json.return_value = {
            "error": {
                "code": "E3001",
                "message": "Model not found",
                "details": {"model": "minicpm-o"},
            }
        }

        error = parse_gateway_error(response)

        assert isinstance(error, GatewayError)
        assert error.code == "E3001"
        assert error.message == "Model not found"
        assert error.details["model"] == "minicpm-o"
        assert error.http_status == 404

    def test_parse_invalid_json(self):
        """JSON 解析失败时使用 HTTP 状态码"""
        response = MagicMock()
        response.status_code = 500
        response.json.side_effect = ValueError("Invalid JSON")

        error = parse_gateway_error(response)

        assert error.code == "HTTP_500"
        assert error.http_status == 500


class TestMapHttpxException:
    """httpx 异常映射测试"""

    def test_timeout(self):
        """TimeoutException → LLMTimeoutError"""
        result = map_httpx_exception(httpx.TimeoutException("timeout"))
        assert isinstance(result, LLMTimeoutError)

    def test_connect_error(self):
        """ConnectError → LLMConnectionError"""
        result = map_httpx_exception(httpx.ConnectError("refused"))
        assert isinstance(result, LLMConnectionError)

    def test_other_exception(self):
        """其他异常 → LLMConnectionError"""
        result = map_httpx_exception(RuntimeError("unknown"))
        assert isinstance(result, LLMConnectionError)

    def test_passthrough_llm_exceptions(self):
        """LLM 异常直接透传"""
        original = LLMTimeoutError("test")
        result = map_httpx_exception(original)
        assert result is original


class TestGatewayRetry:
    """重试装饰器测试"""

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self):
        """连接错误触发重试"""
        call_count = 0

        @gateway_retry(max_retries=2, retry_on=(LLMConnectionError,))
        async def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise LLMConnectionError("test")
            return "success"

        result = await failing_func()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_other_errors(self):
        """非指定异常不重试"""
        call_count = 0

        @gateway_retry(max_retries=3, retry_on=(LLMConnectionError,))
        async def failing_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("should not retry")

        with pytest.raises(ValueError):
            await failing_func()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        """重试耗尽后抛出异常"""
        call_count = 0

        @gateway_retry(max_retries=2, retry_on=(LLMConnectionError,))
        async def always_failing():
            nonlocal call_count
            call_count += 1
            raise LLMConnectionError("always fails")

        with pytest.raises(LLMConnectionError):
            await always_failing()
        assert call_count == 3  # 1 initial + 2 retries


class TestRecordGatewaySpan:
    """Langfuse span 记录测试"""

    @patch("apps.common.gateway_utils.settings")
    def test_skip_when_not_configured(self, mock_settings):
        """未配置 Langfuse 时静默跳过"""
        mock_settings.LANGFUSE_PUBLIC_KEY = ""
        mock_settings.LANGFUSE_SECRET_KEY = ""
        mock_settings.LANGFUSE_HOST = ""

        # 不应抛出异常
        record_gateway_span(
            request_type="tts",
            model="minicpm-o",
            duration=1.0,
            status_code=200,
        )

    @patch("langfuse.Langfuse")
    @patch("apps.common.gateway_utils.settings")
    def test_record_success_span(self, mock_settings, mock_langfuse_cls):
        """记录成功的 span（Langfuse 3.x start_span API）"""
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = "sk-test"
        mock_settings.LANGFUSE_HOST = "http://langfuse:3100"

        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.start_span.return_value = mock_span
        mock_langfuse_cls.return_value = mock_langfuse

        record_gateway_span(
            request_type="tts",
            model="minicpm-o",
            duration=1.5,
            status_code=200,
            request_id="req-abc",
        )

        mock_langfuse.start_span.assert_called_once()
        span_args = mock_langfuse.start_span.call_args
        metadata = span_args[1]["metadata"]
        assert metadata["model"] == "minicpm-o"
        assert metadata["request_type"] == "tts"
        assert metadata["duration"] == 1.5
        assert metadata["status_code"] == 200
        assert metadata["request_id"] == "req-abc"
        mock_span.end.assert_called_once()
        mock_langfuse.flush.assert_called_once()

    @patch("langfuse.Langfuse")
    @patch("apps.common.gateway_utils.settings")
    def test_record_error_span(self, mock_settings, mock_langfuse_cls):
        """记录错误的 span（Langfuse 3.x start_span API）"""
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = "sk-test"
        mock_settings.LANGFUSE_HOST = "http://langfuse:3100"

        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.start_span.return_value = mock_span
        mock_langfuse_cls.return_value = mock_langfuse

        record_gateway_span(
            request_type="inference_cancel",
            model="",
            duration=5.0,
            status_code=504,
            request_id="req-cancel",
            error="timeout",
        )

        span_args = mock_langfuse.start_span.call_args
        metadata = span_args[1]["metadata"]
        assert metadata["error"] == "timeout"
        assert span_args[1]["level"] == "ERROR"
        assert metadata["request_type"] == "inference_cancel"

    @patch("langfuse.Langfuse")
    @patch("apps.common.gateway_utils.settings")
    def test_record_document_parse_span(self, mock_settings, mock_langfuse_cls):
        """记录文档解析 span（Langfuse 3.x start_span API）"""
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = "sk-test"
        mock_settings.LANGFUSE_HOST = "http://langfuse:3100"

        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.start_span.return_value = mock_span
        mock_langfuse_cls.return_value = mock_langfuse

        record_gateway_span(
            request_type="document_parse",
            model="minicpm-v",
            duration=2.3,
            status_code=202,
            request_id="req-doc",
        )

        span_args = mock_langfuse.start_span.call_args
        metadata = span_args[1]["metadata"]
        assert metadata["model"] == "minicpm-v"
        assert metadata["request_type"] == "document_parse"
        assert metadata["status_code"] == 202
