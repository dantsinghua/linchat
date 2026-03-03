"""
媒体过期清理 Celery 任务单元测试

参考:
- specs/008-multimodal-minicpm/tasks.md T066a
- 宪法: 服务层覆盖率 95%+

覆盖场景:
- 过期记录查询逻辑
- MinIO 删除调用验证
- is_expired 标记更新
- 空结果集处理
- 单条 MinIO 删除失败时跳过并保持 is_expired=False
- 连续 10 条失败时终止本轮并记录 CRITICAL
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.media.models import MediaAttachment


class TestCleanExpiredMedia(TestCase):
    """清理过期媒体文件 Celery 任务测试"""

    def _create_attachment(
        self, uuid_suffix: str, expired: bool = True, is_expired: bool = False
    ) -> MediaAttachment:
        """创建测试用媒体附件"""
        now = timezone.now()
        if expired:
            expires_at = now - timedelta(hours=1)
        else:
            expires_at = now + timedelta(days=7)

        return MediaAttachment.objects.create(
            attachment_uuid=f"test-uuid-{uuid_suffix}",
            user_id=1,
            media_type=MediaAttachment.TYPE_IMAGE,
            mime_type="image/jpeg",
            file_name=f"test-{uuid_suffix}.jpg",
            file_size=1024,
            storage_path=f"media/1/2026-02-12/test-uuid-{uuid_suffix}.jpg",
            width=100,
            height=100,
            is_expired=is_expired,
            created_at=now,
            expires_at=expires_at,
        )

    @patch("apps.common.storage.minio_service.minio_service")
    def test_clean_expired_attachments(self, mock_minio: MagicMock) -> None:
        """过期附件被清理：MinIO 文件删除 + is_expired 标记为 True"""
        mock_minio.delete_file.return_value = True

        a1 = self._create_attachment("001")
        a2 = self._create_attachment("002")

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 2
        assert stats["failed"] == 0
        assert stats["aborted"] is False

        # 验证 MinIO 删除调用
        assert mock_minio.delete_file.call_count == 2

        # 验证 is_expired 已更新
        a1.refresh_from_db()
        a2.refresh_from_db()
        assert a1.is_expired is True
        assert a2.is_expired is True

    @patch("apps.common.storage.minio_service.minio_service")
    def test_empty_result_set(self, mock_minio: MagicMock) -> None:
        """无过期附件时正常返回空统计"""
        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 0
        assert stats["failed"] == 0
        assert stats["aborted"] is False
        mock_minio.delete_file.assert_not_called()

    @patch("apps.common.storage.minio_service.minio_service")
    def test_skip_unexpired_attachments(self, mock_minio: MagicMock) -> None:
        """未过期的附件不会被清理"""
        mock_minio.delete_file.return_value = True

        self._create_attachment("active", expired=False)
        expired = self._create_attachment("expired", expired=True)

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 1
        mock_minio.delete_file.call_count == 1

        expired.refresh_from_db()
        assert expired.is_expired is True

    @patch("apps.common.storage.minio_service.minio_service")
    def test_skip_already_expired_attachments(self, mock_minio: MagicMock) -> None:
        """已标记 is_expired=True 的附件不会被重复清理"""
        mock_minio.delete_file.return_value = True

        self._create_attachment("already-done", expired=True, is_expired=True)

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 0
        mock_minio.delete_file.assert_not_called()

    @patch("apps.common.storage.minio_service.minio_service")
    def test_single_minio_failure_skips_and_keeps_is_expired_false(
        self, mock_minio: MagicMock
    ) -> None:
        """单条 MinIO 删除失败：跳过该记录，is_expired 保持 False"""
        mock_minio.delete_file.side_effect = [False, True]

        a1 = self._create_attachment("fail")
        a2 = self._create_attachment("success")

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 1
        assert stats["failed"] == 1
        assert stats["aborted"] is False

        # 失败的记录 is_expired 保持 False
        a1.refresh_from_db()
        assert a1.is_expired is False

        # 成功的记录 is_expired 标记为 True
        a2.refresh_from_db()
        assert a2.is_expired is True

    @patch("apps.common.storage.minio_service.minio_service")
    def test_consecutive_failures_abort(self, mock_minio: MagicMock) -> None:
        """连续 10 条 MinIO 删除失败：终止本轮清理并记录 CRITICAL"""
        mock_minio.delete_file.return_value = False

        # 创建 12 条过期附件
        attachments = []
        for i in range(12):
            attachments.append(self._create_attachment(f"fail-{i:03d}"))

        from apps.chat.tasks import clean_expired_media

        with self.assertLogs("apps.media.tasks", level="CRITICAL") as cm:
            stats = clean_expired_media()

        assert stats["aborted"] is True
        assert stats["failed"] == 10
        assert stats["cleaned"] == 0

        # 验证 CRITICAL 日志
        assert any("连续" in msg and "MinIO" in msg for msg in cm.output)

        # 所有附件 is_expired 保持 False
        for att in attachments:
            att.refresh_from_db()
            assert att.is_expired is False

    @patch("apps.common.storage.minio_service.minio_service")
    def test_consecutive_failure_counter_resets_on_success(
        self, mock_minio: MagicMock
    ) -> None:
        """成功一条后连续失败计数器重置"""
        # 先成功 1 条，再失败 9 条 → 不应中止（需要连续 10 条才中止）
        mock_minio.delete_file.side_effect = [True] + [False] * 9

        for i in range(10):
            self._create_attachment(f"mixed-{i:03d}")

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["aborted"] is False
        assert stats["cleaned"] == 1
        assert stats["failed"] == 9

    @patch("apps.common.storage.minio_service.minio_service")
    def test_minio_delete_called_with_correct_params(
        self, mock_minio: MagicMock
    ) -> None:
        """验证 MinIO 删除调用参数正确"""
        mock_minio.delete_file.return_value = True

        att = self._create_attachment("params-check")

        from apps.chat.tasks import clean_expired_media

        clean_expired_media()

        mock_minio.delete_file.assert_called_once_with(
            bucket="linchat-media",
            object_name=att.storage_path,
        )

    @patch("apps.common.storage.minio_service.minio_service")
    def test_batch_processing(self, mock_minio: MagicMock) -> None:
        """批量处理：超过 batch_size 时多次循环"""
        mock_minio.delete_file.return_value = True

        # 创建 3 条过期附件
        for i in range(3):
            self._create_attachment(f"batch-{i:03d}")

        from apps.chat.tasks import clean_expired_media

        stats = clean_expired_media()

        assert stats["cleaned"] == 3
        assert stats["failed"] == 0
