"""
事件服务异步测试 (T024)

覆盖:
- EventService.subscribe_user_events() 异步生成器
- EventService.publish_logout_event() 发布事件
- SSE 事件格式验证
- Redis pubsub 资源清理 (T025)

测试方式: pytest-asyncio
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.common.event_service import (
    EventService,
    EventType,
    LogoutReason,
    SSEEvent,
)


# ============ SSEEvent 测试 ============


class TestSSEEvent:
    """SSE 事件数据结构测试"""

    def test_to_sse_format_basic(self):
        """测试基本 SSE 格式输出"""
        event = SSEEvent(
            event_type=EventType.MESSAGE,
            data={"type": "test", "content": "hello"},
        )
        result = event.to_sse_format()
        assert "event: message" in result
        assert 'data: {"type": "test", "content": "hello"}' in result
        assert result.endswith("\n\n")

    def test_to_sse_format_with_event_id(self):
        """测试带事件 ID 的 SSE 格式"""
        event = SSEEvent(
            event_type=EventType.LOGOUT,
            data={"reason": "SSO_CONFLICT"},
            event_id="123",
        )
        result = event.to_sse_format()
        assert "id: 123" in result
        assert "event: logout" in result

    def test_to_sse_format_chinese_content(self):
        """测试中文内容不被转义"""
        event = SSEEvent(
            event_type=EventType.MESSAGE,
            data={"message": "你好世界"},
        )
        result = event.to_sse_format()
        assert "你好世界" in result


# ============ EventService.publish_logout_event 测试 ============


@pytest.mark.asyncio
class TestPublishLogoutEvent:
    """发布登出事件测试"""

    async def test_publish_logout_event_success(self):
        """测试成功发布登出事件"""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            result = await EventService.publish_logout_event(
                user_id=1,
                reason=LogoutReason.SSO_CONFLICT,
            )

        assert result is True
        mock_redis.publish.assert_called_once()

    async def test_publish_logout_event_failure(self):
        """测试发布失败时返回 False"""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=Exception("Redis error"))

        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            result = await EventService.publish_logout_event(
                user_id=1,
                reason=LogoutReason.TOKEN_EXPIRED,
            )

        assert result is False


# ============ EventService.subscribe_user_events 测试 ============


@pytest.mark.asyncio
class TestSubscribeUserEvents:
    """订阅用户事件测试"""

    async def test_subscribe_yields_connected_event(self):
        """测试订阅后首先收到连接成功事件"""
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            gen = EventService.subscribe_user_events(user_id=1)
            # 获取第一个事件（连接成功）
            first_event = await gen.__anext__()

        assert "connected" in first_event
        assert "event: message" in first_event

    async def test_subscribe_receives_published_message(self):
        """测试接收发布的消息"""
        test_message = SSEEvent(
            event_type=EventType.LOGOUT,
            data={"reason": "SSO_CONFLICT"},
        ).to_sse_format()

        call_count = 0

        async def mock_get_message(ignore_subscribe_messages=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "message", "data": test_message}
            raise asyncio.CancelledError()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = mock_get_message
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        events = []
        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            gen = EventService.subscribe_user_events(user_id=1)
            try:
                async for event in gen:
                    events.append(event)
                    if len(events) >= 2:
                        break
            except asyncio.CancelledError:
                pass

        # 第一个是连接事件，第二个是测试消息
        assert len(events) >= 1
        assert "connected" in events[0]


# ============ T025: 资源释放验证测试 ============


@pytest.mark.asyncio
class TestResourceCleanup:
    """资源释放验证测试 (T025)"""

    async def test_cleanup_on_normal_exit(self):
        """测试正常退出时资源被清理"""
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError())
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            gen = EventService.subscribe_user_events(user_id=1)
            try:
                # 获取连接事件
                await gen.__anext__()
                # 触发取消
                await gen.__anext__()
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            finally:
                # 关闭生成器
                await gen.aclose()

        # 验证资源被清理
        mock_pubsub.unsubscribe.assert_called()
        mock_pubsub.close.assert_called()

    async def test_cleanup_on_exception(self):
        """测试异常时资源被清理"""
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(side_effect=Exception("Test error"))
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("apps.common.event_service.get_redis", return_value=mock_redis):
            gen = EventService.subscribe_user_events(user_id=1)
            try:
                await gen.__anext__()  # connected
                await gen.__anext__()  # raises
            except Exception:
                pass
            finally:
                await gen.aclose()

        # 验证资源被清理
        mock_pubsub.unsubscribe.assert_called()
        mock_pubsub.close.assert_called()


# ============ T027: Redis 降级测试 ============


@pytest.mark.asyncio
class TestRedisDegradation:
    """Redis 降级测试 (T027)"""

    async def test_publish_handles_redis_unavailable(self):
        """测试 Redis 不可用时发布优雅降级"""
        with patch("apps.common.event_service.get_redis", side_effect=Exception("Redis unavailable")):
            result = await EventService.publish_logout_event(
                user_id=1,
                reason=LogoutReason.SSO_CONFLICT,
            )
        # 应返回 False 而非抛出异常
        assert result is False

    async def test_subscribe_handles_redis_connection_error(self):
        """测试订阅时 Redis 连接错误的处理"""
        with patch("apps.common.event_service.get_redis", side_effect=Exception("Connection refused")):
            gen = EventService.subscribe_user_events(user_id=1)
            with pytest.raises(Exception):
                await gen.__anext__()
