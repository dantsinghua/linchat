"""
DocumentParseService.poll_task_status 重试逻辑单元测试（012-doc-parse-progress T010）

覆盖: GATEWAY_ERROR 自动重试、非 GATEWAY_ERROR 不重试、重试耗尽抛出
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.media.services.document import DocumentParseError, DocumentParseService
from tests.helpers import run_async


class TestPollTaskStatusRetry:
    """poll_task_status GATEWAY_ERROR 重试测试"""

    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.build_gateway_headers", return_value={"Authorization": "Bearer test"})
    @patch.object(DocumentParseService, "_get_gateway_url", return_value="http://gateway:8100")
    @patch.object(DocumentParseService, "_gateway_request", new_callable=AsyncMock)
    def test_success_no_retry(self, mock_request, mock_url, mock_headers, mock_sleep):
        """首次成功 — 无重试"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "completed", "progress": {"current": 10, "total": 10}}
        mock_request.return_value = mock_response

        result = run_async(DocumentParseService.poll_task_status("task-ok"))

        assert result["status"] == "completed"
        assert mock_request.call_count == 1
        mock_sleep.assert_not_called()

    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.build_gateway_headers", return_value={"Authorization": "Bearer test"})
    @patch.object(DocumentParseService, "_get_gateway_url", return_value="http://gateway:8100")
    @patch.object(DocumentParseService, "_gateway_request", new_callable=AsyncMock)
    def test_gateway_error_retry_then_success(self, mock_request, mock_url, mock_headers, mock_sleep):
        """GATEWAY_ERROR 重试 2 次后成功"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "processing"}
        mock_request.side_effect = [
            DocumentParseError("GATEWAY_ERROR", "连接超时"),
            DocumentParseError("GATEWAY_ERROR", "连接重置"),
            mock_response,  # 第 3 次成功
        ]

        result = run_async(DocumentParseService.poll_task_status("task-retry"))

        assert result["status"] == "processing"
        assert mock_request.call_count == 3
        assert mock_sleep.call_count == 2  # 2 次重试等待

    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.build_gateway_headers", return_value={"Authorization": "Bearer test"})
    @patch.object(DocumentParseService, "_get_gateway_url", return_value="http://gateway:8100")
    @patch.object(DocumentParseService, "_gateway_request", new_callable=AsyncMock)
    def test_gateway_error_exhaust_retries(self, mock_request, mock_url, mock_headers, mock_sleep):
        """GATEWAY_ERROR 重试 3 次后仍失败 — 抛出异常"""
        mock_request.side_effect = DocumentParseError("GATEWAY_ERROR", "持续连接失败")

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(DocumentParseService.poll_task_status("task-exhaust"))

        assert exc_info.value.code == "GATEWAY_ERROR"
        assert mock_request.call_count == 4  # 1 + 3 retries
        assert mock_sleep.call_count == 3

    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.build_gateway_headers", return_value={"Authorization": "Bearer test"})
    @patch.object(DocumentParseService, "_get_gateway_url", return_value="http://gateway:8100")
    @patch.object(DocumentParseService, "_gateway_request", new_callable=AsyncMock)
    def test_non_gateway_error_no_retry(self, mock_request, mock_url, mock_headers, mock_sleep):
        """非 GATEWAY_ERROR（如 PARSE_ERROR）— 不重试，直接抛出"""
        mock_request.side_effect = DocumentParseError("PARSE_ERROR", "文档格式不支持")

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(DocumentParseService.poll_task_status("task-no-retry"))

        assert exc_info.value.code == "PARSE_ERROR"
        assert mock_request.call_count == 1
        mock_sleep.assert_not_called()
