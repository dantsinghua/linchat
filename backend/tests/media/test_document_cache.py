"""
DocumentParseService 缓存方法单元测试 (011-document-subagent-rag T020)

覆盖: get_cached_result, save_parsed_result, clear_parsed_cache, force re-parse
覆盖率要求: 服务层 >= 95%
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.media.services.document import DocumentParseService
from tests.helpers import run_async


def _make_attachment(**overrides):
    att = MagicMock()
    att.attachment_id = overrides.get("attachment_id", 1)
    att.attachment_uuid = overrides.get("attachment_uuid", "test-uuid-123")
    att.user_id = overrides.get("user_id", 42)
    att.file_name = overrides.get("file_name", "test.pdf")
    att.parsed_content = overrides.get("parsed_content", None)
    att.parsed_content_path = overrides.get("parsed_content_path", None)
    att.is_expired = overrides.get("is_expired", False)
    return att


class TestGetCachedResult:
    """get_cached_result 测试"""

    def test_cache_hit_db(self):
        """DB parsed_content 非空 → 直接返回"""
        att = _make_attachment(parsed_content="# Hello World")
        result = run_async(DocumentParseService.get_cached_result(att))
        assert result == "# Hello World"

    def test_cache_miss_both_empty(self):
        """parsed_content 和 parsed_content_path 都为空 → None"""
        att = _make_attachment(parsed_content=None, parsed_content_path=None)
        result = run_async(DocumentParseService.get_cached_result(att))
        assert result is None

    @patch("apps.common.storage.minio_service.minio_service")
    def test_cache_fallback_minio(self, mock_minio):
        """DB 为空但 parsed_content_path 非空 → MinIO 降级"""
        mock_minio.download_file.return_value = b"# From MinIO"
        att = _make_attachment(parsed_content=None, parsed_content_path="parsed/42/2026-03-05/uuid.md")
        result = run_async(DocumentParseService.get_cached_result(att))
        assert result == "# From MinIO"

    @patch("apps.common.storage.minio_service.minio_service")
    def test_cache_fallback_minio_fail(self, mock_minio):
        """MinIO 降级失败 → 返回 None"""
        mock_minio.download_file.side_effect = Exception("MinIO down")
        att = _make_attachment(parsed_content=None, parsed_content_path="parsed/42/2026-03-05/uuid.md")
        result = run_async(DocumentParseService.get_cached_result(att))
        assert result is None

    def test_expired_file_still_returns_cached(self):
        """is_expired=True 但有 parsed_content → 仍返回缓存"""
        att = _make_attachment(parsed_content="# Cached content", is_expired=True)
        result = run_async(DocumentParseService.get_cached_result(att))
        assert result == "# Cached content"


class TestSaveParsedResult:
    """save_parsed_result 测试"""

    @patch("apps.common.storage.minio_service.minio_service")
    @patch("apps.media.repositories.media_attachment_repo.update_parsed_cache", new_callable=AsyncMock, return_value=1)
    def test_dual_write_success(self, mock_update, mock_minio):
        """双写成功 — MinIO + DB 都成功"""
        att = _make_attachment()
        result = run_async(DocumentParseService.save_parsed_result(att, "# Content"))
        assert result is True
        mock_minio.upload_bytes.assert_called_once()
        mock_update.assert_called_once()

    @patch("apps.common.storage.minio_service.minio_service")
    def test_minio_upload_fail_skips_db(self, mock_minio):
        """MinIO 上传失败 → 跳过 DB → 返回 False"""
        mock_minio.upload_bytes.side_effect = Exception("upload fail")
        att = _make_attachment()
        result = run_async(DocumentParseService.save_parsed_result(att, "# Content"))
        assert result is False

    @patch("apps.common.storage.minio_service.minio_service")
    @patch("apps.media.repositories.media_attachment_repo.update_parsed_cache", new_callable=AsyncMock, side_effect=Exception("DB error"))
    def test_db_fail_compensates_minio_delete(self, mock_update, mock_minio):
        """DB 写入失败 → 补偿删除 MinIO 文件 → 返回 False"""
        att = _make_attachment()
        result = run_async(DocumentParseService.save_parsed_result(att, "# Content"))
        assert result is False
        mock_minio.delete_file.assert_called_once()


class TestClearParsedCache:
    """clear_parsed_cache 测试"""

    @patch("apps.common.storage.minio_service.minio_service")
    @patch("apps.media.repositories.doc_chunk_repo.delete_by_attachment_id", new_callable=AsyncMock, return_value=5)
    @patch("apps.media.repositories.media_attachment_repo.clear_parsed_cache", new_callable=AsyncMock)
    def test_clear_all(self, mock_clear_db, mock_del_chunks, mock_minio):
        """清除所有缓存 — MinIO + chunks + DB"""
        att = _make_attachment(parsed_content_path="parsed/42/2026-03-05/uuid.md")
        run_async(DocumentParseService.clear_parsed_cache(att))
        mock_minio.delete_file.assert_called_once()
        mock_del_chunks.assert_called_once_with(1)
        mock_clear_db.assert_called_once_with(1)

    @patch("apps.common.storage.minio_service.minio_service")
    @patch("apps.media.repositories.doc_chunk_repo.delete_by_attachment_id", new_callable=AsyncMock, return_value=0)
    @patch("apps.media.repositories.media_attachment_repo.clear_parsed_cache", new_callable=AsyncMock)
    def test_clear_no_minio_path(self, mock_clear_db, mock_del_chunks, mock_minio):
        """parsed_content_path 为空 → 不调用 MinIO delete"""
        att = _make_attachment(parsed_content_path=None)
        run_async(DocumentParseService.clear_parsed_cache(att))
        mock_minio.delete_file.assert_not_called()
        mock_clear_db.assert_called_once()
