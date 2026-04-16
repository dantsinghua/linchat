"""
017-ambient-speaker-id: 未知说话人标签分配测试 (T018)

覆盖:
- _assign_unknown_label: 相同 hash 得到相同标签
- _assign_unknown_label: 不同 hash 得到不同标签（unknown_01, unknown_02）
- _assign_unknown_label: 通过 Redis HGET/HSET 持久化标签
- _assign_unknown_label: INCR 原子计数器
- _retrospective_match: 注册声纹后更新历史消息 speaker_id
- _retrospective_match: 回溯匹配后清理 Redis HDEL
- _retrospective_match: Redis hash 为空时无错误

Mock 策略:
- core.redis.get_async_redis_client → AsyncMock Redis client
  (_assign_unknown_label 在 consumer_events.py 中通过 local import 调用)
- apps.chat.models.Message 通过 patch 替换
  (_retrospective_match 在 speaker_service.py 中通过 local import 引用)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.voice.consumer_events import EventMixin
from apps.voice.services.speaker_service import SpeakerService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock(hget_return=None, incr_return=1, hgetall_return=None):
    """Build a minimal AsyncMock Redis client."""
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=hget_return)
    redis.hset = AsyncMock(return_value=1)
    redis.incr = AsyncMock(return_value=incr_return)
    redis.hdel = AsyncMock(return_value=1)
    redis.hgetall = AsyncMock(return_value=hgetall_return or {})
    return redis


class MockConsumer:
    """Minimal consumer stub for EventMixin method calls."""

    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self._mode = "ambient"
        self._send_json = AsyncMock()


# ===========================================================================
# TestAssignUnknownLabel
# ===========================================================================


class TestAssignUnknownLabel:
    """_assign_unknown_label: Redis 标签分配逻辑

    consumer_events._assign_unknown_label 使用
      `from core.redis import get_async_redis_client`
    as a local import inside the method body.
    Patch target: core.redis.get_async_redis_client
    """

    @pytest.mark.asyncio
    async def test_same_hash_gets_same_label(self):
        """相同 embedding_hash 两次调用 → 两次均返回相同标签"""
        # First call: cache miss → assign new label
        # Second call: cache hit → return existing label
        redis = AsyncMock()
        redis.hget = AsyncMock(side_effect=[None, b"unknown_01"])
        redis.incr = AsyncMock(return_value=1)
        redis.hset = AsyncMock(return_value=1)

        consumer = MockConsumer()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)):
            label1 = await EventMixin._assign_unknown_label(consumer, "hash_abc")
            label2 = await EventMixin._assign_unknown_label(consumer, "hash_abc")

        assert label1 == "unknown_01"
        assert label2 == "unknown_01"

    @pytest.mark.asyncio
    async def test_different_hash_gets_different_label(self):
        """不同 embedding_hash → 不同标签（unknown_01, unknown_02）"""
        redis = AsyncMock()
        redis.hget = AsyncMock(return_value=None)   # always cache miss
        redis.incr = AsyncMock(side_effect=[1, 2])  # successive counters
        redis.hset = AsyncMock(return_value=1)

        consumer = MockConsumer()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)):
            label1 = await EventMixin._assign_unknown_label(consumer, "hash_aaa")
            label2 = await EventMixin._assign_unknown_label(consumer, "hash_bbb")

        assert label1 == "unknown_01"
        assert label2 == "unknown_02"

    @pytest.mark.asyncio
    async def test_label_persists_via_redis_hash(self):
        """验证 HGET 和 HSET 都在 voice:unknown_speakers 键上操作"""
        redis = _make_redis_mock(hget_return=None, incr_return=5)
        consumer = MockConsumer()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)):
            await EventMixin._assign_unknown_label(consumer, "myhash123")

        redis.hget.assert_called_once_with("voice:unknown_speakers", "myhash123")
        redis.hset.assert_called_once_with(
            "voice:unknown_speakers", "myhash123", "unknown_05"
        )

    @pytest.mark.asyncio
    async def test_counter_increments_atomically(self):
        """验证 INCR 在 voice:unknown_counter 键上被调用（原子计数器）"""
        redis = _make_redis_mock(hget_return=None, incr_return=3)
        consumer = MockConsumer()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)):
            label = await EventMixin._assign_unknown_label(consumer, "hash_xyz")

        redis.incr.assert_called_once_with("voice:unknown_counter")
        assert label == "unknown_03"


# ===========================================================================
# TestRetrospectiveMatch
# ===========================================================================


class TestRetrospectiveMatch:
    """SpeakerService._retrospective_match: 回溯匹配历史消息

    _retrospective_match 内部使用 local imports:
      from core.redis import get_async_redis_client
      from apps.chat.models import Message
      from asgiref.sync import sync_to_async
    Patch targets:
      core.redis.get_async_redis_client → mock Redis
      apps.chat.models.Message          → mock model class
      asgiref.sync.sync_to_async        → wrap lambda to return AsyncMock
    """

    @pytest.mark.asyncio
    async def test_retrospective_match_updates_messages(self):
        """注册声纹后，unknown_label 对应的历史消息被更新 speaker_id"""
        redis = _make_redis_mock(
            hgetall_return={b"hash_aaa": b"unknown_01"},
        )

        mock_qs = MagicMock()
        mock_qs.update = MagicMock(return_value=3)  # 3 rows updated
        mock_message_cls = MagicMock()
        mock_message_cls.objects.filter.return_value = mock_qs

        service = SpeakerService()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)), \
             patch("apps.chat.models.Message", mock_message_cls), \
             patch("asgiref.sync.sync_to_async",
                   side_effect=lambda f: AsyncMock(return_value=f())):
            await service._retrospective_match(
                user_id=42, gateway_speaker_id="gw-001", name="张三"
            )

        # filter should have been called (with unknown label)
        mock_message_cls.objects.filter.assert_called()

    @pytest.mark.asyncio
    async def test_retrospective_match_cleans_redis(self):
        """回溯匹配有更新 → HDEL 清理 Redis 对应条目"""
        redis = _make_redis_mock(
            hgetall_return={"hash_aaa": "unknown_01"},
        )

        mock_qs = MagicMock()
        mock_qs.update = MagicMock(return_value=2)
        mock_message_cls = MagicMock()
        mock_message_cls.objects.filter.return_value = mock_qs

        service = SpeakerService()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)), \
             patch("apps.chat.models.Message", mock_message_cls), \
             patch("asgiref.sync.sync_to_async",
                   side_effect=lambda f: AsyncMock(return_value=f())):
            await service._retrospective_match(
                user_id=7, gateway_speaker_id="gw-002", name="李四"
            )

        redis.hdel.assert_called_once_with("voice:unknown_speakers", "hash_aaa")

    @pytest.mark.asyncio
    async def test_retrospective_match_no_entries(self):
        """Redis hash 为空 → 不调用消息更新，无异常"""
        redis = _make_redis_mock(hgetall_return={})

        mock_message_cls = MagicMock()
        service = SpeakerService()

        with patch("core.redis.get_async_redis_client", AsyncMock(return_value=redis)), \
             patch("apps.chat.models.Message", mock_message_cls):
            await service._retrospective_match(
                user_id=1, gateway_speaker_id="gw-003", name="王五"
            )

        mock_message_cls.objects.filter.assert_not_called()
        redis.hdel.assert_not_called()
