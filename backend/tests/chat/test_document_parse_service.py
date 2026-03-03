"""
DocumentParseService 单元测试

覆盖:
- create_parse_task: 创建解析任务
- poll_task_status: 轮询任务状态
- get_task_result: 获取解析结果
- parse_document: 主方法（创建 + 后台轮询）
- _poll_and_notify: 后台轮询 + EventService 推送
- _get_auth_headers: 认证头生成
- _handle_error_response: 错误响应处理

覆盖率要求: 服务层 >= 95%
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.chat.services.document_parse_service import (
    DocumentParseError,
    DocumentParseService,
)


class TestDocumentParseService:
    """DocumentParseService 测试类"""

    # ============ _get_gateway_url 测试 ============

    @patch("apps.media.services.document.settings")
    def test_get_gateway_url_configured(self, mock_settings):
        """测试已配置 Gateway URL"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"

        url = DocumentParseService._get_gateway_url()

        assert url == "http://gateway:8100"

    @patch("apps.media.services.document.settings")
    def test_get_gateway_url_not_configured(self, mock_settings):
        """测试未配置 Gateway URL 抛出异常"""
        mock_settings.LLM_GATEWAY_URL = ""

        with pytest.raises(DocumentParseError) as exc_info:
            DocumentParseService._get_gateway_url()

        assert exc_info.value.code == "GATEWAY_NOT_CONFIGURED"

    # ============ create_parse_task 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.record_gateway_span")
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_create_parse_task_success(
        self, mock_settings, mock_client_class, mock_build_headers, mock_record_span
    ):
        """测试创建解析任务成功"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_settings.LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = 30
        expected_headers = {"Authorization": "Bearer test-key", "X-Request-ID": "req-123"}
        mock_build_headers.return_value = expected_headers

        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "task_id": "abc-123",
            "status": "pending",
            "model": "qwen2.5-vl",
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        result = await DocumentParseService.create_parse_task(
            file_data=b"pdf-content",
            file_name="test.pdf",
            model="qwen2.5-vl",
            pages="1-5",
        )

        assert result["task_id"] == "abc-123"
        assert result["status"] == "pending"
        mock_build_headers.assert_called_once()
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["headers"] == expected_headers

    @pytest.mark.asyncio
    @patch("apps.media.services.document.record_gateway_span")
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_create_parse_task_invalid_file(
        self, mock_settings, mock_client_class, mock_build_headers, mock_record_span
    ):
        """测试创建解析任务失败（不支持的文件格式）"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_settings.LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = 30
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "code": "E6002",
                "message": "不支持的文件格式",
                "type": "validation_error",
            }
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService.create_parse_task(
                file_data=b"txt-content",
                file_name="test.txt",
                model="qwen2.5-vl",
            )

        assert exc_info.value.code == "E6002"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.record_gateway_span")
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_create_parse_task_timeout(
        self, mock_settings, mock_client_class, mock_build_headers, mock_record_span
    ):
        """测试创建解析任务超时"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_settings.LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = 30
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("request timed out")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService.create_parse_task(
                file_data=b"pdf-content",
                file_name="test.pdf",
                model="qwen2.5-vl",
            )

        assert exc_info.value.code == "GATEWAY_TIMEOUT"

    # ============ poll_task_status 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_poll_task_completed(self, mock_settings, mock_client_class, mock_build_headers):
        """测试轮询到完成状态"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "task_id": "abc-123",
            "status": "completed",
            "progress": {"current": 10, "total": 10},
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        result = await DocumentParseService.poll_task_status("abc-123")

        assert result["status"] == "completed"
        assert result["progress"]["current"] == 10

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_poll_task_failed(self, mock_settings, mock_client_class, mock_build_headers):
        """测试轮询到失败状态"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "task_id": "abc-123",
            "status": "failed",
            "error_message": "解析失败",
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        result = await DocumentParseService.poll_task_status("abc-123")

        assert result["status"] == "failed"
        assert result["error_message"] == "解析失败"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_poll_task_not_found(self, mock_settings, mock_client_class, mock_build_headers):
        """测试轮询不存在的任务"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "error": {
                "code": "E6001",
                "message": "任务不存在",
            }
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService.poll_task_status("nonexistent")

        assert exc_info.value.code == "E6001"

    # ============ get_task_result 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_get_result_markdown(self, mock_settings, mock_client_class, mock_build_headers):
        """测试获取 Markdown 结果"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/markdown"}
        mock_response.text = "# Title\n\nContent here"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        result = await DocumentParseService.get_task_result("abc-123", format="markdown")

        assert result == "# Title\n\nContent here"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_get_result_json(self, mock_settings, mock_client_class, mock_build_headers):
        """测试获取 JSON 结果"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        expected_json = {
            "task_id": "abc-123",
            "total_pages": 5,
            "pages": [{"page_number": 1, "markdown": "# Page 1"}],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = expected_json

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        result = await DocumentParseService.get_task_result("abc-123", format="json")

        assert result == expected_json

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_get_result_task_not_completed(self, mock_settings, mock_client_class, mock_build_headers):
        """测试任务未完成时获取结果"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        mock_build_headers.return_value = {"Authorization": "Bearer test-key", "X-Request-ID": "req-1"}

        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {
            "error": {
                "code": "E6009",
                "message": "任务尚未完成",
            }
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService.get_task_result("abc-123")

        assert exc_info.value.code == "E6009"

    # ============ auth_header 验证 ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.build_gateway_headers")
    @patch("apps.media.services.document.httpx.AsyncClient")
    @patch("apps.media.services.document.settings")
    async def test_auth_header_sent(self, mock_settings, mock_client_class, mock_build_headers):
        """测试所有 httpx 请求使用 build_gateway_headers 构建 headers"""
        mock_settings.LLM_GATEWAY_URL = "http://gateway:8100"
        expected_headers = {"Authorization": "Bearer my-secret-key", "X-Request-ID": "req-1"}
        mock_build_headers.return_value = expected_headers

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"task_id": "t1", "status": "completed"}
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_class.return_value = mock_client

        # 测试 poll_task_status
        await DocumentParseService.poll_task_status("t1")
        mock_build_headers.assert_called()
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"] == expected_headers

    # ============ _poll_and_notify 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.EventService.publish_event")
    @patch("apps.media.services.document.DocumentParseService.poll_task_status")
    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.settings")
    async def test_poll_and_notify_completed(
        self, mock_settings, mock_sleep, mock_poll, mock_publish
    ):
        """测试轮询到完成时推送 EventService 事件"""
        mock_settings.DOC_PARSE_POLL_INTERVAL = 1
        mock_settings.DOC_PARSE_POLL_MAX_WAIT = 10

        mock_poll.side_effect = [
            {"status": "processing", "progress": {"current": 3, "total": 10}, "error_message": None},
            {"status": "completed", "progress": {"current": 10, "total": 10}, "error_message": None},
        ]
        mock_publish.return_value = True

        await DocumentParseService._poll_and_notify(user_id=123, task_id="t1")

        assert mock_publish.call_count == 2

        # 第一次推送 processing
        first_call_data = mock_publish.call_args_list[0].kwargs["data"]
        assert first_call_data["status"] == "processing"
        assert first_call_data["task_id"] == "t1"

        # 第二次推送 completed
        second_call_data = mock_publish.call_args_list[1].kwargs["data"]
        assert second_call_data["status"] == "completed"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.EventService.publish_event")
    @patch("apps.media.services.document.DocumentParseService.poll_task_status")
    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.settings")
    async def test_poll_and_notify_timeout(
        self, mock_settings, mock_sleep, mock_poll, mock_publish
    ):
        """测试轮询超时推送失败事件"""
        mock_settings.DOC_PARSE_POLL_INTERVAL = 5
        mock_settings.DOC_PARSE_POLL_MAX_WAIT = 10  # 只能轮询 2 次

        # 始终返回 processing
        mock_poll.return_value = {
            "status": "processing",
            "progress": {"current": 1, "total": 10},
            "error_message": None,
        }
        mock_publish.return_value = True

        await DocumentParseService._poll_and_notify(user_id=123, task_id="t1")

        # 最后一次调用应推送 failed（超时）
        last_call_data = mock_publish.call_args_list[-1].kwargs["data"]
        assert last_call_data["status"] == "failed"
        assert "超时" in last_call_data["error_message"]

    @pytest.mark.asyncio
    @patch("apps.media.services.document.EventService.publish_event")
    @patch("apps.media.services.document.DocumentParseService.poll_task_status")
    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.settings")
    async def test_poll_and_notify_failed(
        self, mock_settings, mock_sleep, mock_poll, mock_publish
    ):
        """测试轮询到失败状态推送事件"""
        mock_settings.DOC_PARSE_POLL_INTERVAL = 1
        mock_settings.DOC_PARSE_POLL_MAX_WAIT = 10

        mock_poll.return_value = {
            "status": "failed",
            "progress": {},
            "error_message": "解析引擎错误",
        }
        mock_publish.return_value = True

        await DocumentParseService._poll_and_notify(user_id=123, task_id="t1")

        assert mock_publish.call_count == 1
        call_data = mock_publish.call_args.kwargs["data"]
        assert call_data["status"] == "failed"
        assert call_data["error_message"] == "解析引擎错误"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.EventService.publish_event")
    @patch("apps.media.services.document.DocumentParseService.poll_task_status")
    @patch("apps.media.services.document.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.media.services.document.settings")
    async def test_progress_event_published(
        self, mock_settings, mock_sleep, mock_poll, mock_publish
    ):
        """测试轮询时通过 EventService.publish_event() 推送 DOC_PARSE_PROGRESS 事件"""
        mock_settings.DOC_PARSE_POLL_INTERVAL = 1
        mock_settings.DOC_PARSE_POLL_MAX_WAIT = 10

        mock_poll.return_value = {
            "status": "completed",
            "progress": {"current": 5, "total": 5},
            "error_message": None,
        }
        mock_publish.return_value = True

        await DocumentParseService._poll_and_notify(user_id=42, task_id="t2")

        # 验证使用了正确的事件类型和用户 ID
        mock_publish.assert_called_with(
            user_id=42,
            event_type="doc_parse_progress",
            data={
                "type": "doc_parse_progress",
                "task_id": "t2",
                "status": "completed",
                "progress": {"current": 5, "total": 5},
                "error_message": None,
            },
        )

    # ============ Gateway 错误响应处理测试（通过 _gateway_request） ============

    @pytest.mark.asyncio
    @patch("apps.media.services.document.record_gateway_span")
    @patch("apps.media.services.document.httpx.AsyncClient")
    async def test_gateway_request_error_with_json(self, mock_client_cls, mock_span):
        """测试 Gateway 返回 JSON 格式错误时解析为 DocumentParseError"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "code": "E3001",
                "message": "模型不是 VL 模型",
                "details": {"available_vl_models": ["qwen2.5-vl"]},
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService._gateway_request(
                method="get", url="http://test/v1/tasks/1",
                headers={"X-Request-ID": "test"}, timeout=10.0,
                success_status=200, request_type="test",
            )

        assert exc_info.value.code == "E3001"

    @pytest.mark.asyncio
    @patch("apps.media.services.document.record_gateway_span")
    @patch("apps.media.services.document.httpx.AsyncClient")
    async def test_gateway_request_error_without_json(self, mock_client_cls, mock_span):
        """测试 Gateway 返回非 JSON 格式错误"""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.side_effect = Exception("not json")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(DocumentParseError) as exc_info:
            await DocumentParseService._gateway_request(
                method="get", url="http://test/v1/tasks/1",
                headers={"X-Request-ID": "test"}, timeout=10.0,
                success_status=200, request_type="test",
            )

        assert exc_info.value.code == "HTTP_503"

    # ============ parse_document 测试 ============

    @pytest.mark.asyncio
    @patch("core.redis.get_redis", new_callable=AsyncMock)
    @patch("apps.media.services.document.asyncio.create_task")
    @patch("apps.media.services.document.DocumentParseService.create_parse_task")
    @patch("apps.media.services.document.settings")
    async def test_parse_document_success(
        self, mock_settings, mock_create, mock_create_task, mock_get_redis
    ):
        """测试 parse_document 主方法成功"""
        mock_settings.MINIO_BUCKET_MEDIA = "media"
        mock_settings.LLM_GATEWAY_DOC_PARSE_MODEL = "minicpm-v"

        # Mock 附件查询
        mock_attachment = MagicMock()
        mock_attachment.user_id = 123
        mock_attachment.media_type = "document"
        mock_attachment.is_expired = False
        mock_attachment.storage_path = "media/123/2026-02-08/test.pdf"
        mock_attachment.file_name = "test.pdf"

        mock_repo = AsyncMock()
        mock_repo.get_by_uuid_any_user.return_value = mock_attachment

        mock_minio = MagicMock()
        mock_minio.download_file.return_value = b"pdf-content"

        # Mock Redis
        mock_redis_client = AsyncMock()
        mock_get_redis.return_value = mock_redis_client

        mock_create.return_value = {
            "task_id": "abc-123",
            "status": "pending",
        }

        with patch("apps.media.repositories.media_attachment_repo", mock_repo):
            with patch("apps.common.storage.minio_service.minio_service", mock_minio):
                result = await DocumentParseService.parse_document(
                    user_id=123,
                    attachment_uuid="test-uuid",
                )

        assert result["task_id"] == "abc-123"
        mock_create_task.assert_called_once()  # 后台轮询已启动
