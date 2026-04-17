"""
Document SubAgent 工具单元测试 (011-document-subagent-rag T023)

覆盖: doc_list 格式化, doc_read 截断, doc_search 结果,
       document_parse 缓存复用, force=True 清除缓存
"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import run_async


def _config(user_id=42, uuids=None, request_id="test-req"):
    """构建 RunnableConfig"""
    return {
        "configurable": {
            "user_id": user_id,
            "attachment_uuids": uuids or [],
            "request_id": request_id,
        }
    }


def _make_attachment(**overrides):
    att = MagicMock()
    att.attachment_id = overrides.get("attachment_id", 1)
    att.attachment_uuid = overrides.get("attachment_uuid", "abc123-uuid")
    att.user_id = overrides.get("user_id", 42)
    att.file_name = overrides.get("file_name", "test.pdf")
    att.file_size = overrides.get("file_size", 1024 * 1024)
    att.media_type = overrides.get("media_type", "document")
    att.parsed_content = overrides.get("parsed_content", None)
    att.parsed_content_path = overrides.get("parsed_content_path", None)
    att.is_expired = overrides.get("is_expired", False)
    att.created_at = MagicMock()
    att.created_at.strftime = MagicMock(return_value="2026-03-05 10:00")
    return att


class TestDocList:
    """doc_list 工具测试"""

    @patch("apps.media.repositories.media_attachment_repo.search_documents", new_callable=AsyncMock)
    def test_returns_formatted_list(self, mock_search):
        """返回格式化文档列表"""
        from apps.graph.subagents.document_agent import doc_list

        mock_search.return_value = [
            _make_attachment(file_name="paper.pdf", parsed_content="# Content"),
            _make_attachment(attachment_uuid="def456-uuid", file_name="report.docx", parsed_content=None, is_expired=True),
        ]
        result = run_async(doc_list.ainvoke({"task": "列出文档"}, config=_config()))
        assert "2 个文档" in result
        assert "paper.pdf" in result
        assert "report.docx" in result

    @patch("apps.media.repositories.media_attachment_repo.search_documents", new_callable=AsyncMock)
    def test_empty_list(self, mock_search):
        """无文档返回提示"""
        from apps.graph.subagents.document_agent import doc_list

        mock_search.return_value = []
        result = run_async(doc_list.ainvoke({"task": "列出文档"}, config=_config()))
        assert "没有找到" in result


class TestDocRead:
    """doc_read 工具测试"""

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuid", new_callable=AsyncMock)
    def test_returns_content(self, mock_get, mock_cache):
        from apps.graph.subagents.document_agent import doc_read

        mock_get.return_value = _make_attachment()
        mock_cache.return_value = "# Document Content"
        result = run_async(doc_read.ainvoke({"attachment_uuid": "abc123"}, config=_config()))
        assert "Document Content" in result

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuid", new_callable=AsyncMock)
    def test_truncates_long_content(self, mock_get, mock_cache):
        from apps.graph.subagents.document_agent import doc_read

        mock_get.return_value = _make_attachment()
        mock_cache.return_value = "A" * 10000
        result = run_async(doc_read.ainvoke({"attachment_uuid": "abc123", "max_length": 100}, config=_config()))
        assert "内容已截断" in result

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuid", new_callable=AsyncMock)
    def test_not_parsed(self, mock_get, mock_cache):
        from apps.graph.subagents.document_agent import doc_read

        mock_get.return_value = _make_attachment()
        mock_cache.return_value = None
        result = run_async(doc_read.ainvoke({"attachment_uuid": "abc123"}, config=_config()))
        assert "尚未解析" in result

    @patch("apps.media.repositories.media_attachment_repo.get_by_uuid", new_callable=AsyncMock)
    def test_not_found(self, mock_get):
        """文档不存在"""
        from apps.graph.subagents.document_agent import doc_read

        mock_get.return_value = None
        result = run_async(doc_read.ainvoke({"attachment_uuid": "nonexist"}, config=_config()))
        assert "不存在" in result


class TestDocSearch:
    """doc_search 工具测试"""

    @patch("apps.media.services.document_rag.search_documents_rag", new_callable=AsyncMock)
    def test_returns_results(self, mock_search):
        from apps.graph.subagents.document_agent import doc_search

        mock_search.return_value = [
            {"file_name": "paper.pdf", "attachment_uuid": "abc123", "score": 0.85, "chunk_text": "量子计算相关内容", "match_type": "hybrid", "created_at": ""},
        ]
        result = run_async(doc_search.ainvoke({"query": "量子计算"}, config=_config()))
        assert "1 个相关片段" in result
        assert "paper.pdf" in result

    @patch("apps.media.services.document_rag.search_documents_rag", new_callable=AsyncMock)
    def test_empty_results(self, mock_search):
        from apps.graph.subagents.document_agent import doc_search

        mock_search.return_value = []
        result = run_async(doc_search.ainvoke({"query": "不存在"}, config=_config()))
        assert "未找到匹配内容" in result


class TestDocumentParse:
    """document_parse 工具测试"""

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_cache_hit(self, mock_get_uuids, mock_cache):
        """缓存命中 — 跳过 GPU 锁和 Gateway"""
        from apps.graph.subagents.document_agent import document_parse

        mock_get_uuids.return_value = [_make_attachment(parsed_content="# Cached")]
        mock_cache.return_value = "# Cached Content"

        result = run_async(document_parse.ainvoke({"task": "解析文档"}, config=_config(uuids=["uuid-1"])))
        assert "缓存" in result
        assert "Cached Content" in result

    def test_no_attachments(self):
        """无附件返回提示"""
        from apps.graph.subagents.document_agent import document_parse

        result = run_async(document_parse.ainvoke({"task": "解析文档"}, config=_config(uuids=[])))
        assert "没有用户上传的附件" in result

    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_no_document_type(self, mock_get_uuids):
        """无文档类型附件"""
        from apps.graph.subagents.document_agent import document_parse

        att = _make_attachment(media_type="image")
        mock_get_uuids.return_value = [att]

        result = run_async(document_parse.ainvoke({"task": "解析文档"}, config=_config(uuids=["uuid-1"])))
        assert "没有需要解析的文档" in result

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.services.document_cache.clear_parsed_cache", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_force_clears_cache(self, mock_get_uuids, mock_clear, mock_cache):
        """force=True — 先清除缓存"""
        from apps.graph.subagents.document_agent import document_parse

        mock_get_uuids.return_value = [_make_attachment(is_expired=True)]
        mock_cache.return_value = None  # After clear, no cache

        result = run_async(document_parse.ainvoke({"task": "解析文档", "force": True}, config=_config(uuids=["uuid-1"])))
        mock_clear.assert_called_once()

    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_expired_file_no_cache(self, mock_get_uuids, mock_cache):
        """已过期且无缓存 → 无法解析"""
        from apps.graph.subagents.document_agent import document_parse

        mock_get_uuids.return_value = [_make_attachment(is_expired=True)]
        mock_cache.return_value = None

        result = run_async(document_parse.ainvoke({"task": "解析文档"}, config=_config(uuids=["uuid-1"])))
        assert "过期" in result


class TestDocumentParseSSEProgress:
    """document_parse SSE 进度推送测试（012-doc-parse-progress T009）"""

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.graph.subagents.document_parse_helpers.EventService.publish_event", new_callable=AsyncMock)
    @patch("apps.media.services.document_cache.save_parsed_result", new_callable=AsyncMock, return_value=True)
    @patch("apps.media.services.document.DocumentParseService.get_task_result", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.poll_task_status", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.parse_document", new_callable=AsyncMock)
    @patch("apps.graph.services.acquire_gpu_lock")
    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock, return_value=None)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_sse_completed_flow(self, mock_uuids, mock_cache, mock_gpu, mock_parse,
                                mock_poll, mock_result, mock_save, mock_publish, mock_sleep):
        """completed 流程：pending → processing → completed 共 3 次 SSE 推送"""
        from contextlib import asynccontextmanager
        from apps.graph.subagents.document_agent import document_parse

        @asynccontextmanager
        async def _noop_lock(_req_id):
            yield
        mock_gpu.return_value = _noop_lock("test")

        mock_uuids.return_value = [_make_attachment()]
        mock_parse.return_value = {"task_id": "task-abc"}
        mock_poll.side_effect = [
            {"status": "processing", "progress": {"current": 5, "total": 10}, "suggestion": None, "error_message": None},
            {"status": "completed", "progress": {"current": 10, "total": 10}, "suggestion": None, "error_message": None},
        ]
        mock_result.return_value = "# Parsed Content"

        result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

        assert "Parsed Content" in result
        # pending(1) + processing(1) + completed(1) = 3 次
        assert mock_publish.call_count == 3
        calls = mock_publish.call_args_list
        assert calls[0].kwargs["data"]["status"] == "pending"
        assert calls[1].kwargs["data"]["status"] == "processing"
        assert calls[2].kwargs["data"]["status"] == "completed"

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.graph.subagents.document_parse_helpers.EventService.publish_event", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.poll_task_status", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.parse_document", new_callable=AsyncMock)
    @patch("apps.graph.services.acquire_gpu_lock")
    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock, return_value=None)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_sse_failed_flow(self, mock_uuids, mock_cache, mock_gpu, mock_parse,
                             mock_poll, mock_publish, mock_sleep):
        """failed 流程：pending → failed 共 2 次 SSE 推送"""
        from contextlib import asynccontextmanager
        from apps.graph.subagents.document_agent import document_parse

        @asynccontextmanager
        async def _noop_lock(_req_id):
            yield
        mock_gpu.return_value = _noop_lock("test")

        mock_uuids.return_value = [_make_attachment()]
        mock_parse.return_value = {"task_id": "task-fail"}
        mock_poll.return_value = {"status": "failed", "progress": {}, "suggestion": None, "error_message": "OCR 引擎异常"}

        result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

        assert "解析失败" in result
        assert "OCR 引擎异常" in result
        # pending(1) + failed(1) = 2 次
        assert mock_publish.call_count == 2
        calls = mock_publish.call_args_list
        assert calls[0].kwargs["data"]["status"] == "pending"
        assert calls[1].kwargs["data"]["status"] == "failed"

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.graph.subagents.document_parse_helpers.EventService.publish_event", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.poll_task_status", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.parse_document", new_callable=AsyncMock)
    @patch("apps.graph.services.acquire_gpu_lock")
    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock, return_value=None)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    @patch("apps.graph.subagents.document_parse_helpers.settings")
    def test_sse_timeout_as_failed(self, mock_settings, mock_uuids, mock_cache, mock_gpu,
                                   mock_parse, mock_poll, mock_publish, mock_sleep):
        """超时 → SSE 推送 failed 状态（012-doc-parse-progress T004）"""
        from contextlib import asynccontextmanager
        from apps.graph.subagents.document_agent import document_parse

        @asynccontextmanager
        async def _noop_lock(_req_id):
            yield
        mock_gpu.return_value = _noop_lock("test")

        # 设置极短的 max_wait 使其超时
        mock_settings.DOC_PARSE_POLL_INTERVAL = 1
        mock_settings.DOC_PARSE_POLL_MAX_WAIT = 2
        mock_settings.DOC_PARSE_MAX_RESULT_LENGTH = 6000

        mock_uuids.return_value = [_make_attachment()]
        mock_parse.return_value = {"task_id": "task-timeout"}
        # 一直返回 processing，不会触发 break
        mock_poll.return_value = {"status": "processing", "progress": {"current": 1, "total": 10}, "suggestion": None, "error_message": None}

        result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

        assert "超时" in result
        # 最后一次 publish 应该是 timeout-as-failed
        last_call = mock_publish.call_args_list[-1]
        assert last_call.kwargs["data"]["status"] == "failed"
        assert "超时" in last_call.kwargs["data"]["error_message"]

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.graph.subagents.document_parse_helpers.EventService.publish_event", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.get_task_result", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.poll_task_status", new_callable=AsyncMock)
    @patch("apps.media.services.document.DocumentParseService.parse_document", new_callable=AsyncMock)
    @patch("apps.graph.services.acquire_gpu_lock")
    @patch("apps.media.services.document_cache.get_cached_result", new_callable=AsyncMock, return_value=None)
    @patch("apps.media.repositories.media_attachment_repo.get_by_uuids", new_callable=AsyncMock)
    def test_sse_incomplete_flow(self, mock_uuids, mock_cache, mock_gpu, mock_parse,
                                 mock_poll, mock_result, mock_publish, mock_sleep):
        """incomplete 流程：pending → incomplete + 获取部分结果（T006）"""
        from contextlib import asynccontextmanager
        from apps.graph.subagents.document_agent import document_parse

        @asynccontextmanager
        async def _noop_lock(_req_id):
            yield
        mock_gpu.return_value = _noop_lock("test")

        mock_uuids.return_value = [_make_attachment()]
        mock_parse.return_value = {"task_id": "task-inc"}
        # current==total → 触发 "INCOMPLETE (final)" 分支（document_parse_helpers.py:97-100），
        # 而非 "INCOMPLETE but progressing" 的 continue 分支（L92-96）
        mock_poll.return_value = {"status": "incomplete", "progress": {"current": 10, "total": 10}, "suggestion": "建议拆分文档", "error_message": None}
        mock_result.return_value = "# Partial Content"

        result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

        assert "部分解析" in result
        assert "建议拆分文档" in result
        assert "Partial Content" in result
        # pending(1) + incomplete(1) = 2 次
        assert mock_publish.call_count == 2
