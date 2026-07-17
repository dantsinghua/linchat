"""
基础性能冒烟测试

测试场景:
- SC-002 初步验证：大模型首令牌延迟 < 3秒（开发环境允许50%误差，即 < 4.5秒）
- SC-003 初步验证：流式字符延迟 < 200ms（开发环境允许100%误差，即 < 400ms）
- SC-005 初步验证：历史消息加载（50条）< 3秒

目的：确保架构设计不存在根本性性能问题
工具：pytest + httpx（不需要 locust）
完整性能测试延迟到 Phase 7 T068-T070

注意：
- 此测试为冒烟测试，关注架构层面的性能问题
- 使用 mock 模拟外部依赖，确保测试可重复
- 实际 LLM 响应延迟取决于网络和服务端负载
"""
import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import TestCase

from apps.chat.services import (
    ChatService,
    HistoryService,
    StreamChunk,
    MessageVO,
)
from apps.chat.models import Message


def run_async(coro):
    """运行异步函数"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestFirstTokenLatency(TestCase):
    """SC-002: 首令牌延迟测试"""

    @patch("apps.graph.services.AgentService.execute")
    def test_first_token_latency(self, mock_execute):
        """SC-002: 首令牌延迟 < 4.5秒（开发环境允许50%误差）"""

        async def mock_stream(*args, **kwargs):
            await asyncio.sleep(0.1)
            yield StreamChunk(type="content", content="Hello", message_id=1)
            yield StreamChunk(type="content", content=" World", message_id=1)
            yield StreamChunk(type="done", content="", message_id=1)

        mock_execute.return_value = mock_stream()

        async def test():
            start_time = time.time()
            first_token_time = None

            async for chunk in ChatService.send_message(user_id=1, content="test"):
                if chunk.type == "content" and first_token_time is None:
                    first_token_time = time.time()
                    break

            return first_token_time - start_time if first_token_time else None

        latency = run_async(test())

        # SC-002: 首令牌延迟 < 4.5秒
        self.assertIsNotNone(latency)
        self.assertLess(latency, 4.5, f"首令牌延迟 {latency:.2f}s 超过阈值 4.5s")
        print(f"SC-002 首令牌延迟: {latency * 1000:.2f}ms")


class TestStreamingLatency(TestCase):
    """SC-003: 流式字符延迟测试"""

    @patch("apps.graph.services.AgentService.execute")
    def test_streaming_latency(self, mock_execute):
        """SC-003: 流式字符延迟 < 400ms（开发环境允许100%误差）"""

        async def mock_stream(*args, **kwargs):
            tokens = ["Hello", " ", "World", "!", " How", " are", " you", "?"]
            for token in tokens:
                await asyncio.sleep(0.05)
                yield StreamChunk(type="content", content=token, message_id=1)
            yield StreamChunk(type="done", content="", message_id=1)

        mock_execute.return_value = mock_stream()

        async def test():
            token_times = []
            prev_time = time.time()

            async for chunk in ChatService.send_message(user_id=1, content="test"):
                if chunk.type == "content":
                    current_time = time.time()
                    token_times.append(current_time - prev_time)
                    prev_time = current_time

            return token_times

        latencies = run_async(test())

        # 计算平均延迟（排除首 token）
        if len(latencies) > 1:
            avg_latency = sum(latencies[1:]) / len(latencies[1:])
            max_latency = max(latencies[1:])

            # SC-003: 平均延迟 < 400ms，最大延迟 < 400ms
            self.assertLess(
                avg_latency, 0.4,
                f"平均流式延迟 {avg_latency * 1000:.2f}ms 超过阈值 400ms"
            )
            self.assertLess(
                max_latency, 0.4,
                f"最大流式延迟 {max_latency * 1000:.2f}ms 超过阈值 400ms"
            )
            print(f"SC-003 平均流式延迟: {avg_latency * 1000:.2f}ms, 最大: {max_latency * 1000:.2f}ms")


class TestHistoryLoadLatency(TestCase):
    """SC-005: 历史消息加载延迟测试"""

    @patch("apps.chat.services.chat_service.message_repo.find_latest_by_user")
    def test_history_load_latency_50_messages(self, mock_find):
        """SC-005: 50条历史消息加载 < 3秒"""
        # 模拟 50 条消息
        mock_messages = []
        for i in range(50):
            msg = MagicMock()
            msg.message_id = i + 1
            msg.message_uuid = f"uuid-{i}"
            msg.role = "user" if i % 2 == 0 else "assistant"
            msg.content = f"Message content {i} " + "x" * 100  # 模拟较长内容
            msg.status = 1
            msg.sequence = i + 1
            msg.created_time = datetime.now()
            msg.request_id = f"req-{i}"
            msg.model_name = "model" if i % 2 == 1 else None
            msg.response_time_ms = 100 if i % 2 == 1 else None
            mock_messages.append(msg)

        # 模拟数据库查询延迟
        async def mock_query(*args, **kwargs):
            await asyncio.sleep(0.05)  # 50ms 查询延迟
            return mock_messages

        mock_find.side_effect = mock_query

        async def test():
            start_time = time.time()
            messages = await HistoryService.load_messages(user_id=1, limit=50)
            end_time = time.time()
            return end_time - start_time, len(messages)

        latency, count = run_async(test())

        # SC-005: 加载时间 < 3秒
        self.assertLess(latency, 3.0, f"历史加载延迟 {latency:.2f}s 超过阈值 3s")
        self.assertEqual(count, 50, f"返回消息数量 {count} 不等于 50")
        print(f"SC-005 历史加载延迟（50条）: {latency * 1000:.2f}ms")

    @patch("apps.chat.services.chat_service.message_repo.find_latest_by_user")
    def test_history_load_latency_100_messages(self, mock_find):
        """额外测试：100条历史消息加载"""
        # 模拟 100 条消息
        mock_messages = []
        for i in range(100):
            msg = MagicMock()
            msg.message_id = i + 1
            msg.message_uuid = f"uuid-{i}"
            msg.role = "user" if i % 2 == 0 else "assistant"
            msg.content = f"Message content {i} " + "x" * 200
            msg.status = 1
            msg.sequence = i + 1
            msg.created_time = datetime.now()
            msg.request_id = f"req-{i}"
            msg.model_name = "model" if i % 2 == 1 else None
            msg.response_time_ms = 100 if i % 2 == 1 else None
            mock_messages.append(msg)

        # 限制为100条
        mock_find.return_value = mock_messages

        async def test():
            start_time = time.time()
            # limit=200 会被限制为 100
            messages = await HistoryService.load_messages(user_id=1, limit=200)
            end_time = time.time()
            return end_time - start_time, len(messages)

        latency, count = run_async(test())

        # 100条也应该在合理时间内完成
        self.assertLess(latency, 5.0, f"历史加载延迟 {latency:.2f}s 超过阈值 5s")
        self.assertEqual(count, 100, f"返回消息数量 {count} 不等于 100")
        print(f"额外测试 历史加载延迟（100条）: {latency * 1000:.2f}ms")


class TestServiceLayerOverhead(TestCase):
    """服务层开销测试"""

    def test_message_vo_conversion_performance(self):
        """测试 MessageVO 转换性能"""
        # 创建 100 个 mock 消息
        mock_messages = []
        for i in range(100):
            msg = MagicMock()
            msg.message_id = i + 1
            msg.message_uuid = f"uuid-{i}"
            msg.role = "user"
            msg.content = f"Content {i}"
            msg.status = 1
            msg.sequence = i + 1
            msg.created_time = datetime.now()
            msg.request_id = f"req-{i}"
            msg.model_name = None
            msg.response_time_ms = None
            mock_messages.append(msg)

        # 测试转换性能（纯 CPU 操作用 process_time，避免共享主机负载导致 wall-time 抖动）
        start_time = time.process_time()
        vos = [MessageVO.from_entity(m) for m in mock_messages]
        end_time = time.process_time()

        latency = (end_time - start_time) * 1000  # 转换为毫秒

        # 100 条消息转换应该在 200ms 内完成
        self.assertLess(latency, 200, f"VO 转换延迟 {latency:.2f}ms 超过阈值 200ms")
        self.assertEqual(len(vos), 100)
        print(f"VO 转换延迟（100条）: {latency:.2f}ms")

    def test_stream_chunk_creation_performance(self):
        """测试 StreamChunk 创建性能"""
        start_time = time.process_time()

        # 创建 1000 个 StreamChunk
        chunks = []
        for i in range(1000):
            chunk = StreamChunk(
                type="content",
                content=f"Token {i}",
                message_id=1
            )
            chunks.append(chunk)

        end_time = time.process_time()
        latency = (end_time - start_time) * 1000

        # 1000 个 chunk 创建应该在 50ms 内完成
        self.assertLess(latency, 50, f"StreamChunk 创建延迟 {latency:.2f}ms 超过阈值 50ms")
        self.assertEqual(len(chunks), 1000)
        print(f"StreamChunk 创建延迟（1000个）: {latency:.2f}ms")


class TestValidationOverhead(TestCase):
    """验证逻辑开销测试"""

    def test_message_length_validation_performance(self):
        """测试消息长度验证性能"""
        from django.conf import settings

        # 测试不同长度消息的验证性能
        test_cases = [
            ("短消息", "Hello"),
            ("中等消息", "x" * 1000),
            ("长消息", "x" * 3999),
            ("边界消息", "x" * settings.MAX_MESSAGE_LENGTH),
        ]

        for name, content in test_cases:
            start_time = time.process_time()

            # 执行 1000 次验证
            for _ in range(1000):
                trimmed = content.strip()
                is_empty = len(trimmed) == 0
                is_too_long = len(content) > settings.MAX_MESSAGE_LENGTH

            end_time = time.process_time()
            latency = (end_time - start_time) * 1000

            # 1000 次验证应该在 10ms 内完成
            self.assertLess(latency, 10, f"{name} 验证延迟 {latency:.2f}ms 超过阈值 10ms")
            print(f"{name} 验证延迟（1000次）: {latency:.2f}ms")


class TestGenerationManagementOverhead(TestCase):
    """生成会话管理开销测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    def test_registration_performance(self):
        """测试会话注册性能"""
        from apps.chat.services import register_generation, unregister_generation

        # 测试 100 次注册
        start_time = time.process_time()
        request_ids = []
        for i in range(100):
            req_id = f"perf-test-{i}"
            register_generation(req_id)
            request_ids.append(req_id)

        end_time = time.process_time()
        register_latency = (end_time - start_time) * 1000

        # 测试 100 次取消注册
        start_time = time.process_time()
        for req_id in request_ids:
            unregister_generation(req_id)

        end_time = time.process_time()
        unregister_latency = (end_time - start_time) * 1000

        # 100 次操作应该在 10ms 内完成
        self.assertLess(
            register_latency, 10,
            f"注册延迟 {register_latency:.2f}ms 超过阈值 10ms"
        )
        self.assertLess(
            unregister_latency, 10,
            f"取消注册延迟 {unregister_latency:.2f}ms 超过阈值 10ms"
        )
        print(f"会话注册延迟（100次）: {register_latency:.2f}ms")
        print(f"会话取消注册延迟（100次）: {unregister_latency:.2f}ms")
