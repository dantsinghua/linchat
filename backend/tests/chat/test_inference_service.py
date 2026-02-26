"""
InferenceService 单元测试

参考: specs/008-multimodal-minicpm/tasks.md#T015

覆盖:
- get_active_task: 获取当前进行中的推理任务
- register_task: 注册新推理任务（原子性并发控制）
- complete_task: 完成推理任务
- cancel_task: 取消推理任务
- refresh_task_ttl: 刷新任务 TTL

覆盖率要求: 服务层 ≥ 95%
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone

from apps.chat.services.inference_service import InferenceService, _get_inference_task_key
from apps.chat.services.types import InferenceTask


class TestInferenceService:
    """InferenceService 测试类"""

    @pytest.fixture
    def inference_service(self):
        """创建 InferenceService 实例"""
        return InferenceService()

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 客户端"""
        return AsyncMock()

    @pytest.fixture
    def sample_task(self):
        """创建示例推理任务"""
        return InferenceTask(
            request_id="test-request-123",
            model="minicpm-v",
            started_at=timezone.now(),
            media_types=["image"],
        )

    # ============ _get_inference_task_key 测试 ============

    def test_get_inference_task_key(self):
        """测试获取 Redis 键"""
        key = _get_inference_task_key(123)
        assert key == "user:123:inference_task"

    # ============ get_active_task 测试 ============

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_get_active_task_exists(self, mock_get_redis, inference_service, sample_task):
        """测试获取存在的活跃任务"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = sample_task.to_json()
        mock_get_redis.return_value = mock_redis

        result = await inference_service.get_active_task(user_id=123)

        assert result is not None
        assert result.request_id == sample_task.request_id
        assert result.model == sample_task.model
        mock_redis.get.assert_called_once_with("user:123:inference_task")

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_get_active_task_not_exists(self, mock_get_redis, inference_service):
        """测试获取不存在的活跃任务"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = await inference_service.get_active_task(user_id=123)

        assert result is None

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_get_active_task_error(self, mock_get_redis, inference_service):
        """测试获取任务 Redis 错误"""
        mock_get_redis.side_effect = Exception("Redis connection error")

        result = await inference_service.get_active_task(user_id=123)

        assert result is None

    # ============ register_task 测试 ============

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_register_task_success(self, mock_get_redis, inference_service):
        """测试注册任务成功"""
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        result = await inference_service.register_task(
            user_id=123,
            request_id="test-request",
            model="minicpm-v",
            media_types=["image"],
        )

        assert result is True
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args.kwargs["nx"] is True
        assert call_args.kwargs["ex"] == 300  # 默认 TTL

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_register_task_already_exists(self, mock_get_redis, inference_service):
        """测试注册任务但已存在（并发冲突）"""
        mock_redis = AsyncMock()
        mock_redis.set.return_value = False  # SETNX 失败
        mock_get_redis.return_value = mock_redis

        result = await inference_service.register_task(
            user_id=123,
            request_id="test-request",
            model="minicpm-v",
        )

        assert result is False

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_register_task_error(self, mock_get_redis, inference_service):
        """测试注册任务 Redis 错误"""
        mock_get_redis.side_effect = Exception("Redis connection error")

        result = await inference_service.register_task(
            user_id=123,
            request_id="test-request",
            model="minicpm-v",
        )

        assert result is False

    # ============ complete_task 测试 ============

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_complete_task_success(self, mock_get_redis, inference_service, sample_task):
        """测试完成任务成功"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = sample_task.to_json()
        mock_redis.delete.return_value = 1
        mock_get_redis.return_value = mock_redis

        result = await inference_service.complete_task(
            user_id=123,
            request_id=sample_task.request_id,
        )

        assert result is True
        mock_redis.delete.assert_called_once_with("user:123:inference_task")

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_complete_task_not_found(self, mock_get_redis, inference_service):
        """测试完成任务但任务不存在"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = await inference_service.complete_task(
            user_id=123,
            request_id="non-existent",
        )

        assert result is False

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_complete_task_request_id_mismatch(self, mock_get_redis, inference_service, sample_task):
        """测试完成任务但请求 ID 不匹配"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = sample_task.to_json()
        mock_get_redis.return_value = mock_redis

        result = await inference_service.complete_task(
            user_id=123,
            request_id="wrong-request-id",
        )

        assert result is False
        mock_redis.delete.assert_not_called()

    # ============ cancel_task 测试 ============

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.EventService.publish_event")
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_cancel_task_success(
        self, mock_get_redis, mock_publish_event, inference_service, sample_task
    ):
        """测试取消任务成功（三步取消：Redis删除 + 进程信号 + Pub/Sub）"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = sample_task.to_json()
        mock_redis.delete.return_value = 1
        mock_get_redis.return_value = mock_redis
        mock_publish_event.return_value = True

        success, cancelled_id = await inference_service.cancel_task(user_id=123)

        assert success is True
        assert cancelled_id == sample_task.request_id
        mock_publish_event.assert_called_once()
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_cancel_task_not_found(self, mock_get_redis, inference_service):
        """测试取消任务但无进行中任务"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        success, cancelled_id = await inference_service.cancel_task(user_id=123)

        assert success is False
        assert cancelled_id is None

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_cancel_task_request_id_mismatch(
        self, mock_get_redis, inference_service, sample_task
    ):
        """测试取消任务但请求 ID 不匹配"""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = sample_task.to_json()
        mock_get_redis.return_value = mock_redis

        success, cancelled_id = await inference_service.cancel_task(
            user_id=123,
            request_id="wrong-request-id",
        )

        assert success is False
        assert cancelled_id is None

    # ============ refresh_task_ttl 测试 ============

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_refresh_task_ttl_success(self, mock_get_redis, inference_service):
        """测试刷新任务 TTL 成功"""
        mock_redis = AsyncMock()
        mock_redis.expire.return_value = True
        mock_get_redis.return_value = mock_redis

        result = await inference_service.refresh_task_ttl(user_id=123)

        assert result is True
        mock_redis.expire.assert_called_once_with("user:123:inference_task", 300)

    @pytest.mark.asyncio
    @patch("apps.chat.services.inference_service.get_redis")
    async def test_refresh_task_ttl_error(self, mock_get_redis, inference_service):
        """测试刷新任务 TTL 错误"""
        mock_get_redis.side_effect = Exception("Redis connection error")

        result = await inference_service.refresh_task_ttl(user_id=123)

        assert result is False


class TestInferenceTask:
    """InferenceTask 数据类测试"""

    def test_to_json(self):
        """测试序列化为 JSON"""
        task = InferenceTask(
            request_id="test-123",
            model="minicpm-v",
            started_at=datetime(2026, 2, 8, 10, 30, 0),
            media_types=["image", "video"],
        )

        json_str = task.to_json()
        assert '"request_id": "test-123"' in json_str
        assert '"model": "minicpm-v"' in json_str
        assert '"media_types": ["image", "video"]' in json_str

    def test_from_json(self):
        """测试从 JSON 反序列化"""
        json_str = '{"request_id": "test-123", "model": "minicpm-v", "started_at": "2026-02-08T10:30:00", "media_types": ["image"]}'

        task = InferenceTask.from_json(json_str)

        assert task.request_id == "test-123"
        assert task.model == "minicpm-v"
        assert task.media_types == ["image"]

    def test_from_json_without_media_types(self):
        """测试从 JSON 反序列化（无 media_types 字段）"""
        json_str = '{"request_id": "test-123", "model": "minicpm-v", "started_at": "2026-02-08T10:30:00"}'

        task = InferenceTask.from_json(json_str)

        assert task.media_types == []

    def test_elapsed_seconds(self):
        """测试计算已运行时长"""
        # 使用一个固定的过去时间
        past_time = timezone.now() - timezone.timedelta(seconds=30)
        task = InferenceTask(
            request_id="test-123",
            model="minicpm-v",
            started_at=past_time,
            media_types=[],
        )

        elapsed = task.elapsed_seconds()

        assert 29 <= elapsed <= 31  # 允许一定误差
