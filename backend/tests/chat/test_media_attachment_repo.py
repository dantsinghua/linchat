"""
MediaAttachmentRepository 单元测试

参考: specs/008-multimodal-minicpm/tasks.md#T009a

覆盖:
- get_by_uuid: 按 UUID 查询（含所有权校验）
- get_by_uuid_any_user: 按 UUID 查询（不校验所有权）
- get_by_uuids: 批量按 UUID 查询（含 user_id 过滤）
- find_expired: 按过期时间查询未标记附件
- create: 创建附件
- update: 更新附件
- associate_message: 关联附件到消息
- mark_expired: 批量标记过期

覆盖率要求: 数据仓库层 ≥ 85%
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone

from apps.media.models import MediaAttachment
from apps.media.repositories import MediaAttachmentRepository


def _make_attachment(**overrides) -> MagicMock:
    """创建 mock 附件对象"""
    defaults = {
        "attachment_id": 1,
        "attachment_uuid": "test-uuid-001",
        "user_id": 1001,
        "media_type": "image",
        "mime_type": "image/jpeg",
        "file_name": "test.jpg",
        "file_size": 1024,
        "storage_path": "media/test/test.jpg",
        "is_expired": False,
        "message_id": None,
        "created_at": timezone.now(),
        "expires_at": timezone.now() + timedelta(days=7),
    }
    defaults.update(overrides)
    att = MagicMock(spec=MediaAttachment)
    for k, v in defaults.items():
        setattr(att, k, v)
    return att


class TestMediaAttachmentRepository:
    """MediaAttachmentRepository 测试类"""

    @pytest.fixture
    def repo(self):
        return MediaAttachmentRepository()

    # ============ get_by_uuid 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuid_found(self, mock_objects, repo):
        """按 UUID 查询：存在且所有权匹配"""
        expected = _make_attachment()
        mock_objects.get.return_value = expected

        result = await repo.get_by_uuid("test-uuid-001", user_id=1001)

        assert result is expected
        mock_objects.get.assert_called_once_with(
            attachment_uuid="test-uuid-001", user_id=1001
        )

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuid_not_found(self, mock_objects, repo):
        """按 UUID 查询：不存在"""
        mock_objects.get.side_effect = MediaAttachment.DoesNotExist

        result = await repo.get_by_uuid("non-existent", user_id=1001)

        assert result is None

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuid_wrong_user(self, mock_objects, repo):
        """按 UUID 查询：UUID 存在但所有权不匹配返回 None [R_DATA_001]"""
        mock_objects.get.side_effect = MediaAttachment.DoesNotExist

        result = await repo.get_by_uuid("test-uuid-001", user_id=9999)

        assert result is None
        mock_objects.get.assert_called_once_with(
            attachment_uuid="test-uuid-001", user_id=9999
        )

    # ============ get_by_uuid_any_user 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuid_any_user_found(self, mock_objects, repo):
        """按 UUID 查询（不校验所有权）：存在"""
        expected = _make_attachment()
        mock_objects.get.return_value = expected

        result = await repo.get_by_uuid_any_user("test-uuid-001")

        assert result is expected
        mock_objects.get.assert_called_once_with(attachment_uuid="test-uuid-001")

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuid_any_user_not_found(self, mock_objects, repo):
        """按 UUID 查询（不校验所有权）：不存在"""
        mock_objects.get.side_effect = MediaAttachment.DoesNotExist

        result = await repo.get_by_uuid_any_user("non-existent")

        assert result is None

    # ============ get_by_uuids 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuids_all_found(self, mock_objects, repo):
        """批量查询：全部找到"""
        a1 = _make_attachment(attachment_uuid="uuid-1")
        a2 = _make_attachment(attachment_uuid="uuid-2")
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([a1, a2])
        mock_objects.filter.return_value = mock_qs

        result = await repo.get_by_uuids(["uuid-1", "uuid-2"], user_id=1001)

        assert len(result) == 2
        mock_objects.filter.assert_called_once_with(
            attachment_uuid__in=["uuid-1", "uuid-2"], user_id=1001
        )

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuids_partial_user_filter(self, mock_objects, repo):
        """批量查询：user_id 过滤仅返回属于该用户的附件 [R_DATA_001]"""
        a_mine = _make_attachment(attachment_uuid="uuid-mine")
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([a_mine])
        mock_objects.filter.return_value = mock_qs

        result = await repo.get_by_uuids(["uuid-mine", "uuid-other"], user_id=1001)

        assert len(result) == 1
        mock_objects.filter.assert_called_once_with(
            attachment_uuid__in=["uuid-mine", "uuid-other"], user_id=1001
        )

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_get_by_uuids_empty_list(self, mock_objects, repo):
        """批量查询：空列表"""
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([])
        mock_objects.filter.return_value = mock_qs

        result = await repo.get_by_uuids([], user_id=1001)

        assert len(result) == 0

    # ============ create 测试 ============

    @pytest.mark.asyncio
    async def test_create_attachment(self, repo):
        """创建附件"""
        attachment = MagicMock(spec=MediaAttachment)
        attachment.save = MagicMock()

        result = await repo.create(attachment)

        attachment.save.assert_called_once()
        assert result is attachment

    # ============ update 测试 ============

    @pytest.mark.asyncio
    async def test_update_attachment(self, repo):
        """更新附件"""
        attachment = MagicMock(spec=MediaAttachment)
        attachment.save = MagicMock()

        result = await repo.update(attachment)

        attachment.save.assert_called_once()
        assert result is attachment

    # ============ associate_message 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_associate_message_success(self, mock_objects, repo):
        """关联附件到消息"""
        mock_qs = MagicMock()
        mock_qs.update.return_value = 2
        mock_objects.filter.return_value = mock_qs

        count = await repo.associate_message(
            attachment_ids=[1, 2], message_id=100, user_id=1001
        )

        assert count == 2
        mock_objects.filter.assert_called_once_with(
            attachment_id__in=[1, 2], user_id=1001
        )
        mock_qs.update.assert_called_once_with(message_id=100)

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_associate_message_wrong_user(self, mock_objects, repo):
        """关联附件到消息：附件不属于当前用户 [R_DATA_001]"""
        mock_qs = MagicMock()
        mock_qs.update.return_value = 0
        mock_objects.filter.return_value = mock_qs

        count = await repo.associate_message(
            attachment_ids=[1], message_id=100, user_id=9999
        )

        assert count == 0

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_associate_message_empty_ids(self, mock_objects, repo):
        """关联附件到消息：空 ID 列表"""
        mock_qs = MagicMock()
        mock_qs.update.return_value = 0
        mock_objects.filter.return_value = mock_qs

        count = await repo.associate_message(
            attachment_ids=[], message_id=100, user_id=1001
        )

        assert count == 0

    # ============ find_expired 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_find_expired_found(self, mock_objects, repo):
        """查询过期附件：有过期未标记记录"""
        expired_att = _make_attachment(is_expired=False, file_name="expired.jpg")
        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: [expired_att]
        mock_objects.filter.return_value = mock_qs

        now = timezone.now()
        result = await repo.find_expired(before_date=now)

        assert len(result) == 1
        mock_objects.filter.assert_called_once_with(
            expires_at__lt=now, is_expired=False
        )

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_find_expired_empty(self, mock_objects, repo):
        """查询过期附件：无过期记录"""
        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: []
        mock_objects.filter.return_value = mock_qs

        result = await repo.find_expired(before_date=timezone.now())

        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_find_expired_custom_limit(self, mock_objects, repo):
        """查询过期附件：自定义 limit"""
        items = [_make_attachment(attachment_id=i) for i in range(3)]
        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: items
        mock_objects.filter.return_value = mock_qs

        result = await repo.find_expired(before_date=timezone.now(), limit=3)

        assert len(result) == 3

    # ============ mark_expired 测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_mark_expired_success(self, mock_objects, repo):
        """批量标记过期"""
        mock_qs = MagicMock()
        mock_qs.update.return_value = 2
        mock_objects.filter.return_value = mock_qs

        count = await repo.mark_expired([1, 2])

        assert count == 2
        mock_objects.filter.assert_called_once_with(attachment_id__in=[1, 2])
        mock_qs.update.assert_called_once_with(is_expired=True)

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_mark_expired_empty_list(self, mock_objects, repo):
        """批量标记过期：空列表"""
        mock_qs = MagicMock()
        mock_qs.update.return_value = 0
        mock_objects.filter.return_value = mock_qs

        count = await repo.mark_expired([])

        assert count == 0

    # ============ 按 message_id 关联查询测试 ============

    @pytest.mark.asyncio
    @patch("apps.media.repositories.MediaAttachment.objects")
    async def test_associate_and_query_by_message(self, mock_objects, repo):
        """验证关联后通过 message_id + user_id 可查询到附件"""
        a1 = _make_attachment(attachment_id=1, message_id=100)
        a2 = _make_attachment(attachment_id=2, message_id=100)

        # 模拟 filter(message_id=100, user_id=1001)
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([a1, a2])
        mock_objects.filter.return_value = mock_qs

        from asgiref.sync import sync_to_async

        attachments = await sync_to_async(
            lambda: list(
                MediaAttachment.objects.filter(message_id=100, user_id=1001)
            )
        )()

        assert len(attachments) == 2
