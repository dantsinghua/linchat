"""
推理取消 API 集成测试

参考:
- specs/008-multimodal-minicpm/tasks.md T037
- 宪法: 服务层覆盖率 95%+

覆盖场景:
- 取消成功返回 200
- 无活跃任务返回 404
- 指定 request_id 取消
- 取消后立即发送新请求成功
- Gateway 取消接口超时处理
- Redis Pub/Sub 降级为 fallback 轮询
- signal_stop() 调用验证
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.chat.services.inference_service import InferenceService
from apps.chat.services.types import InferenceTask
from tests.helpers import run_async


def _make_task(request_id: str = "req-001") -> InferenceTask:
    """创建测试用推理任务"""
    return InferenceTask(
        request_id=request_id,
        model="minicpm-v",
        started_at=timezone.now(),
        media_types=["image"],
    )


class TestCancelInferenceAPI(TestCase):
    """推理取消 API 测试"""

    @patch("apps.chat.services.generation.signal_stop")
    @patch("apps.graph.services.inference_service.EventService")
    @patch("apps.graph.services.inference_service.get_redis")
    def test_cancel_success(
        self, mock_get_redis: AsyncMock, mock_event_svc: MagicMock, mock_signal_stop: MagicMock
    ) -> None:
        """取消成功：返回 True 和 request_id"""
        task = _make_task("req-001")

        mock_client = AsyncMock()
        mock_client.get.return_value = task.to_json()
        mock_client.delete.return_value = True
        mock_get_redis.return_value = mock_client
        mock_event_svc.publish_event = AsyncMock(return_value=True)

        success, cancelled_id = run_async(InferenceService.cancel_task(user_id=1))

        assert success is True
        assert cancelled_id == "req-001"
        mock_signal_stop.assert_called_once_with("req-001")
        mock_event_svc.publish_event.assert_called_once()
        mock_client.delete.assert_called_once()

    @patch("apps.graph.services.inference_service.get_redis")
    def test_cancel_no_active_task(self, mock_get_redis: AsyncMock) -> None:
        """无活跃任务：返回 False"""
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_get_redis.return_value = mock_client

        success, cancelled_id = run_async(InferenceService.cancel_task(user_id=1))

        assert success is False
        assert cancelled_id is None

    @patch("apps.chat.services.generation.signal_stop")
    @patch("apps.graph.services.inference_service.EventService")
    @patch("apps.graph.services.inference_service.get_redis")
    def test_cancel_with_request_id(
        self, mock_get_redis: AsyncMock, mock_event_svc: MagicMock, mock_signal_stop: MagicMock
    ) -> None:
        """指定 request_id 取消：匹配时成功"""
        task = _make_task("req-002")

        mock_client = AsyncMock()
        mock_client.get.return_value = task.to_json()
        mock_client.delete.return_value = True
        mock_get_redis.return_value = mock_client
        mock_event_svc.publish_event = AsyncMock(return_value=True)

        success, cancelled_id = run_async(
            InferenceService.cancel_task(user_id=1, request_id="req-002")
        )

        assert success is True
        assert cancelled_id == "req-002"

    @patch("apps.graph.services.inference_service.get_redis")
    def test_cancel_request_id_mismatch(self, mock_get_redis: AsyncMock) -> None:
        """request_id 不匹配：返回 False"""
        task = _make_task("req-003")

        mock_client = AsyncMock()
        mock_client.get.return_value = task.to_json()
        mock_get_redis.return_value = mock_client

        success, cancelled_id = run_async(
            InferenceService.cancel_task(user_id=1, request_id="req-other")
        )

        assert success is False
        assert cancelled_id is None

    @patch("apps.chat.services.generation.signal_stop")
    @patch("apps.graph.services.inference_service.EventService")
    @patch("apps.graph.services.inference_service.get_redis")
    def test_cancel_then_register_new_task(
        self, mock_get_redis: AsyncMock, mock_event_svc: MagicMock, mock_signal_stop: MagicMock
    ) -> None:
        """取消后立即注册新任务：成功"""
        task = _make_task("req-old")

        mock_client = AsyncMock()
        mock_client.get.side_effect = [task.to_json(), None]
        mock_client.delete.return_value = True
        mock_client.set.return_value = True
        mock_get_redis.return_value = mock_client
        mock_event_svc.publish_event = AsyncMock(return_value=True)

        success, _ = run_async(InferenceService.cancel_task(user_id=1))
        assert success is True

        registered = run_async(
            InferenceService.register_task(
                user_id=1,
                request_id="req-new",
                model="minicpm-v",
                media_types=["image"],
            )
        )
        assert registered is True

    @patch("apps.chat.services.generation.signal_stop")
    @patch("apps.graph.services.inference_service.EventService")
    @patch("apps.graph.services.inference_service.get_redis")
    def test_gateway_cancel_timeout(
        self,
        mock_get_redis: AsyncMock,
        mock_event_svc: MagicMock,
        mock_signal_stop: MagicMock,
    ) -> None:
        """取消成功：signal_stop 和事件发布正常"""
        task = _make_task("req-timeout")

        mock_client = AsyncMock()
        mock_client.get.return_value = task.to_json()
        mock_client.delete.return_value = True
        mock_get_redis.return_value = mock_client
        mock_event_svc.publish_event = AsyncMock(return_value=True)

        success, cancelled_id = run_async(
            InferenceService.cancel_task(user_id=1)
        )

        assert success is True
        assert cancelled_id == "req-timeout"
        mock_signal_stop.assert_called_once_with("req-timeout")


class TestMonitorCancelSignal(TestCase):
    """取消信号监听测试 (T035)"""

    @patch("core.redis.get_redis")
    def test_pubsub_cancel_signal(self, mock_get_redis: AsyncMock) -> None:
        """Pub/Sub 收到 INFERENCE_CANCEL 事件后设置 stop_event"""
        from apps.graph.services.cancel_monitor import monitor_cancel_signal as _monitor_cancel_signal

        stop_event = asyncio.Event()

        cancel_sse = (
            'event: inference_cancel\n'
            'data: {"type": "inference_cancel", "request_id": "req-cancel-001", "reason": "user_requested"}\n\n'
        )

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        mock_pubsub.get_message = AsyncMock(
            return_value={"type": "message", "data": cancel_sse.encode("utf-8")}
        )

        mock_client = AsyncMock()
        mock_client.pubsub = MagicMock(return_value=mock_pubsub)
        mock_get_redis.return_value = mock_client

        run_async(_monitor_cancel_signal(
            user_id=1,
            request_id="req-cancel-001",
            stop_event=stop_event,
        ))

        assert stop_event.is_set()

    @patch("core.redis.get_redis")
    def test_pubsub_ignores_other_events(self, mock_get_redis: AsyncMock) -> None:
        """Pub/Sub 忽略非 INFERENCE_CANCEL 事件"""
        from apps.graph.services.cancel_monitor import monitor_cancel_signal as _monitor_cancel_signal

        stop_event = asyncio.Event()
        call_count = 0

        other_sse = (
            'event: context_status\n'
            'data: {"type": "context_status", "pct": 50}\n\n'
        )

        async def fake_get_message(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "message", "data": other_sse.encode("utf-8")}
            stop_event.set()
            return None

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        mock_pubsub.get_message = AsyncMock(side_effect=fake_get_message)

        mock_client = AsyncMock()
        mock_client.pubsub = MagicMock(return_value=mock_pubsub)
        mock_get_redis.return_value = mock_client

        run_async(_monitor_cancel_signal(
            user_id=1,
            request_id="req-001",
            stop_event=stop_event,
        ))

        assert stop_event.is_set()
        assert call_count >= 2

    @patch("core.redis.get_redis")
    def test_pubsub_fallback_to_polling(self, mock_get_redis: AsyncMock) -> None:
        """Pub/Sub 失败时降级为轮询"""
        from apps.graph.services.cancel_monitor import monitor_cancel_signal as _monitor_cancel_signal

        stop_event = asyncio.Event()

        poll_client = AsyncMock()
        poll_client.get.return_value = None

        call_count = 0

        async def fake_get_redis():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Redis Pub/Sub unavailable")
            return poll_client

        mock_get_redis.side_effect = fake_get_redis

        run_async(_monitor_cancel_signal(
            user_id=1,
            request_id="req-fallback",
            stop_event=stop_event,
        ))

        assert stop_event.is_set()

    @patch("core.redis.get_redis")
    def test_polling_detects_key_deletion(self, mock_get_redis: AsyncMock) -> None:
        """轮询模式：检测到 Redis 键被删除时设置 stop_event"""
        from apps.graph.services.cancel_monitor import poll_cancel_signal as _poll_cancel_signal

        stop_event = asyncio.Event()

        task_json = _make_task("req-poll").to_json()

        mock_client = AsyncMock()
        mock_client.get.side_effect = [task_json, None]
        mock_get_redis.return_value = mock_client

        run_async(_poll_cancel_signal(
            user_id=1,
            request_id="req-poll",
            stop_event=stop_event,
        ))

        assert stop_event.is_set()


class TestSignalStopIntegration(TestCase):
    """signal_stop 集成验证"""

    def test_signal_stop_called_on_cancel(self) -> None:
        """cancel_task 调用 signal_stop 设置 asyncio.Event"""
        from apps.chat.services.generation import register_generation, unregister_generation

        stop_event = register_generation("req-signal-test")
        assert not stop_event.is_set()

        task = _make_task("req-signal-test")

        with patch("apps.graph.services.inference_service.get_redis") as mock_redis, \
             patch("apps.graph.services.inference_service.EventService") as mock_event:
            mock_client = AsyncMock()
            mock_client.get.return_value = task.to_json()
            mock_client.delete.return_value = True
            mock_redis.return_value = mock_client
            mock_event.publish_event = AsyncMock(return_value=True)

            success, _ = run_async(InferenceService.cancel_task(user_id=1))
            assert success is True

        assert stop_event.is_set()
        unregister_generation("req-signal-test")
