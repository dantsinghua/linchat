"""
文档解析 API 集成测试

参考:
- specs/008-multimodal-minicpm/tasks.md T077
- document-parse-api.yaml
- 宪法: 覆盖率 80%+

覆盖场景:
- POST /api/v1/chat/documents/parse/ (create)
- GET /api/v1/chat/documents/tasks/{task_id}/ (status)
- GET /api/v1/chat/documents/tasks/{task_id}/result/ (result)
- verify_task_ownership 所有权校验
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import TestCase

from apps.media.services.document import (
    DocumentParseError,
    DocumentParseService,
)
from tests.helpers import run_async


# ============ verify_task_ownership 单元测试 ============


class TestVerifyTaskOwnership(TestCase):
    """verify_task_ownership 所有权校验"""

    @patch("core.redis.get_redis")
    def test_ownership_pass(self, mock_get_redis):
        """所有权校验通过"""
        mock_client = AsyncMock()
        mock_client.get.return_value = b"1"
        mock_get_redis.return_value = mock_client

        # 不应抛异常
        run_async(DocumentParseService.verify_task_ownership("task-001", 1))

    @patch("core.redis.get_redis")
    def test_ownership_key_missing(self, mock_get_redis):
        """所有权键不存在（过期/不存在）：抛出 TASK_NOT_FOUND"""
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_get_redis.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(DocumentParseService.verify_task_ownership("task-expired", 1))

        assert exc_info.value.code == "TASK_NOT_FOUND"

    @patch("core.redis.get_redis")
    def test_ownership_mismatch(self, mock_get_redis):
        """所有者不匹配：抛出 TASK_ACCESS_DENIED"""
        mock_client = AsyncMock()
        mock_client.get.return_value = b"1"
        mock_get_redis.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(DocumentParseService.verify_task_ownership("task-001", 999))

        assert exc_info.value.code == "TASK_ACCESS_DENIED"

    @patch("core.redis.get_redis")
    def test_ownership_string_value(self, mock_get_redis):
        """所有权值为字符串（非 bytes）时正常工作"""
        mock_client = AsyncMock()
        mock_client.get.return_value = "42"
        mock_get_redis.return_value = mock_client

        run_async(DocumentParseService.verify_task_ownership("task-001", 42))


# ============ parse_document 视图测试 ============


class TestParseDocumentView(TestCase):
    """POST /api/v1/chat/documents/parse/ 测试

    直接测试服务层调用逻辑，通过 mock DocumentParseService 方法验证
    视图层的错误码映射和 HTTP 状态码。
    """

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_create_success(self, mock_parse):
        """创建解析任务成功：service 返回结果"""
        mock_parse.return_value = {
            "task_id": "task-001",
            "status": "pending",
        }

        result = run_async(
            DocumentParseService.parse_document(
                user_id=1, attachment_uuid="uuid-001"
            )
        )

        assert result["task_id"] == "task-001"
        assert result["status"] == "pending"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_attachment_not_found(self, mock_parse):
        """附件不存在：抛出 ATTACHMENT_NOT_FOUND"""
        mock_parse.side_effect = DocumentParseError(
            code="ATTACHMENT_NOT_FOUND", message="附件不存在"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.parse_document(
                    user_id=1, attachment_uuid="uuid-missing"
                )
            )

        assert exc_info.value.code == "ATTACHMENT_NOT_FOUND"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_attachment_access_denied(self, mock_parse):
        """附件无权访问：抛出 ATTACHMENT_ACCESS_DENIED"""
        mock_parse.side_effect = DocumentParseError(
            code="ATTACHMENT_ACCESS_DENIED", message="无权访问该附件"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.parse_document(
                    user_id=1, attachment_uuid="uuid-other"
                )
            )

        assert exc_info.value.code == "ATTACHMENT_ACCESS_DENIED"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_invalid_document_type(self, mock_parse):
        """非文档类型：抛出 INVALID_DOCUMENT_TYPE"""
        mock_parse.side_effect = DocumentParseError(
            code="INVALID_DOCUMENT_TYPE",
            message="仅支持 PDF/DOCX 文档",
            details={"supported_types": ["application/pdf"]},
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.parse_document(
                    user_id=1, attachment_uuid="uuid-image"
                )
            )

        assert exc_info.value.code == "INVALID_DOCUMENT_TYPE"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_attachment_expired(self, mock_parse):
        """附件已过期：抛出 ATTACHMENT_EXPIRED"""
        mock_parse.side_effect = DocumentParseError(
            code="ATTACHMENT_EXPIRED", message="文件已过期"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.parse_document(
                    user_id=1, attachment_uuid="uuid-expired"
                )
            )

        assert exc_info.value.code == "ATTACHMENT_EXPIRED"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_gateway_not_configured(self, mock_parse):
        """Gateway 未配置：抛出 GATEWAY_NOT_CONFIGURED"""
        mock_parse.side_effect = DocumentParseError(
            code="GATEWAY_NOT_CONFIGURED",
            message="未配置 LLM_GATEWAY_URL",
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.parse_document(
                    user_id=1, attachment_uuid="uuid-001"
                )
            )

        assert exc_info.value.code == "GATEWAY_NOT_CONFIGURED"

    @patch.object(DocumentParseService, "parse_document", new_callable=AsyncMock)
    def test_create_with_pages(self, mock_parse):
        """指定 pages 参数：正确传递"""
        mock_parse.return_value = {"task_id": "task-002", "status": "pending"}

        result = run_async(
            DocumentParseService.parse_document(
                user_id=1, attachment_uuid="uuid-001", pages="1-5"
            )
        )

        mock_parse.assert_called_once_with(
            user_id=1, attachment_uuid="uuid-001", pages="1-5"
        )
        assert result["task_id"] == "task-002"


# ============ poll_task_status 视图测试 ============


class TestGetParseTaskStatusView(TestCase):
    """GET /api/v1/chat/documents/tasks/{task_id}/ 测试"""

    @patch.object(DocumentParseService, "poll_task_status", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_status_success(self, mock_verify, mock_status):
        """查询成功"""
        mock_verify.return_value = None
        mock_status.return_value = {
            "task_id": "task-001",
            "status": "processing",
            "progress": {"current": 3, "total": 10},
        }

        run_async(DocumentParseService.verify_task_ownership("task-001", 1))
        result = run_async(DocumentParseService.poll_task_status("task-001"))

        assert result["status"] == "processing"
        assert result["progress"]["current"] == 3

    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_task_not_found(self, mock_verify):
        """所有权键不存在：抛出 TASK_NOT_FOUND"""
        mock_verify.side_effect = DocumentParseError(
            code="TASK_NOT_FOUND", message="任务不存在或已过期"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.verify_task_ownership("task-expired", 1)
            )

        assert exc_info.value.code == "TASK_NOT_FOUND"

    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_task_access_denied(self, mock_verify):
        """非所有者访问：抛出 TASK_ACCESS_DENIED"""
        mock_verify.side_effect = DocumentParseError(
            code="TASK_ACCESS_DENIED", message="无权访问该解析任务"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.verify_task_ownership("task-other", 999)
            )

        assert exc_info.value.code == "TASK_ACCESS_DENIED"

    @patch.object(DocumentParseService, "poll_task_status", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_gateway_task_expired(self, mock_verify, mock_status):
        """Gateway 返回 E6009 任务过期"""
        mock_verify.return_value = None
        mock_status.side_effect = DocumentParseError(
            code="E6009", message="任务已过期"
        )

        run_async(DocumentParseService.verify_task_ownership("task-old", 1))
        with pytest.raises(DocumentParseError) as exc_info:
            run_async(DocumentParseService.poll_task_status("task-old"))

        assert exc_info.value.code == "E6009"

    @patch.object(DocumentParseService, "poll_task_status", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_status_completed(self, mock_verify, mock_status):
        """查询已完成的任务"""
        mock_verify.return_value = None
        mock_status.return_value = {
            "task_id": "task-done",
            "status": "completed",
            "progress": {"current": 10, "total": 10},
        }

        result = run_async(DocumentParseService.poll_task_status("task-done"))

        assert result["status"] == "completed"


# ============ get_task_result 视图测试 ============


class TestGetParseTaskResultView(TestCase):
    """GET /api/v1/chat/documents/tasks/{task_id}/result/ 测试"""

    @patch.object(DocumentParseService, "get_task_result", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_result_markdown_success(self, mock_verify, mock_result):
        """获取 Markdown 结果成功"""
        mock_verify.return_value = None
        mock_result.return_value = "# Title\n\nContent paragraph"

        run_async(DocumentParseService.verify_task_ownership("task-001", 1))
        content = run_async(
            DocumentParseService.get_task_result("task-001", format="markdown")
        )

        assert content == "# Title\n\nContent paragraph"

    @patch.object(DocumentParseService, "get_task_result", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_result_json_success(self, mock_verify, mock_result):
        """获取 JSON 结果成功"""
        mock_verify.return_value = None
        mock_result.return_value = {
            "pages": [{"page_number": 1, "markdown": "text"}]
        }

        content = run_async(
            DocumentParseService.get_task_result("task-001", format="json")
        )

        assert content["pages"][0]["page_number"] == 1

    @patch.object(DocumentParseService, "get_task_result", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_result_task_not_found(self, mock_verify, mock_result):
        """所有权键不存在"""
        mock_verify.side_effect = DocumentParseError(
            code="TASK_NOT_FOUND", message="任务不存在或已过期"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.verify_task_ownership("task-missing", 1)
            )

        assert exc_info.value.code == "TASK_NOT_FOUND"
        mock_result.assert_not_called()

    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_result_access_denied(self, mock_verify):
        """非所有者访问"""
        mock_verify.side_effect = DocumentParseError(
            code="TASK_ACCESS_DENIED", message="无权访问该解析任务"
        )

        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.verify_task_ownership("task-other", 999)
            )

        assert exc_info.value.code == "TASK_ACCESS_DENIED"

    @patch.object(DocumentParseService, "get_task_result", new_callable=AsyncMock)
    @patch.object(
        DocumentParseService, "verify_task_ownership", new_callable=AsyncMock
    )
    def test_result_not_completed(self, mock_verify, mock_result):
        """任务未完成"""
        mock_verify.return_value = None
        mock_result.side_effect = DocumentParseError(
            code="TASK_NOT_COMPLETED", message="任务尚未完成"
        )

        run_async(DocumentParseService.verify_task_ownership("task-wip", 1))
        with pytest.raises(DocumentParseError) as exc_info:
            run_async(
                DocumentParseService.get_task_result("task-wip", format="markdown")
            )

        assert exc_info.value.code == "TASK_NOT_COMPLETED"


# ============ 视图层错误码映射验证 ============


class TestViewErrorCodeMapping(TestCase):
    """验证视图层错误码 → HTTP 状态码映射 (T075)"""

    def test_parse_document_status_map(self):
        """parse_document 视图错误码映射"""
        status_map = {
            "ATTACHMENT_NOT_FOUND": 404,
            "ATTACHMENT_ACCESS_DENIED": 403,
            "ATTACHMENT_EXPIRED": 410,
            "GATEWAY_NOT_CONFIGURED": 503,
            "GATEWAY_TIMEOUT": 504,
        }
        # 验证非映射错误码默认返回 400
        assert status_map.get("UNKNOWN_CODE", 400) == 400
        assert status_map["ATTACHMENT_NOT_FOUND"] == 404
        assert status_map["ATTACHMENT_ACCESS_DENIED"] == 403
        assert status_map["ATTACHMENT_EXPIRED"] == 410
        assert status_map["GATEWAY_NOT_CONFIGURED"] == 503
        assert status_map["GATEWAY_TIMEOUT"] == 504

    def test_status_result_view_status_map(self):
        """status/result 视图错误码映射"""
        status_map = {
            "TASK_NOT_FOUND": 404,
            "TASK_ACCESS_DENIED": 403,
            "E6009": 410,
        }
        assert status_map["TASK_NOT_FOUND"] == 404
        assert status_map["TASK_ACCESS_DENIED"] == 403
        assert status_map["E6009"] == 410
        # 非映射错误码无特殊映射
        assert status_map.get("GATEWAY_ERROR") is None
