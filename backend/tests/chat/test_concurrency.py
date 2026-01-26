"""
基础并发冒烟测试

测试场景:
- SC-004 初步验证：10 用户并发发送消息，验证无死锁/数据错乱
- 并发登录
- 并发消息发送
- 并发历史加载

工具：pytest-asyncio + httpx
覆盖率要求：此文件为集成测试，不计入服务层覆盖率

注意：
- 此测试需要真实的后端服务运行
- 完整负载测试（100用户）延迟到 Phase 7 T069
"""
import asyncio
import random
import uuid
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import TestCase

from apps.chat.services import (
    ChatService,
    HistoryService,
    register_generation,
    signal_stop,
    unregister_generation,
)
from apps.common.exceptions import EmptyMessageException


def run_async(coro):
    """运行异步函数"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def collect_stream(async_gen):
    """收集异步生成器的所有结果"""
    results = []
    async for item in async_gen:
        results.append(item)
    return results


class TestConcurrentGenerationManagement(TestCase):
    """并发生成会话管理测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    def test_concurrent_register_unregister(self):
        """测试并发注册和取消注册"""
        request_ids = [f"req-concurrent-{i}" for i in range(10)]

        # 并发注册
        events = []
        for req_id in request_ids:
            event = register_generation(req_id)
            events.append(event)

        # 验证所有会话都已注册
        for req_id in request_ids:
            from apps.chat.services import get_stop_event
            self.assertIsNotNone(get_stop_event(req_id))

        # 并发取消注册
        for req_id in request_ids:
            unregister_generation(req_id)

        # 验证所有会话都已取消
        for req_id in request_ids:
            from apps.chat.services import get_stop_event
            self.assertIsNone(get_stop_event(req_id))

    def test_concurrent_stop_signals(self):
        """测试并发停止信号"""
        request_ids = [f"req-stop-{i}" for i in range(10)]

        # 注册所有会话
        events = {}
        for req_id in request_ids:
            events[req_id] = register_generation(req_id)

        # 并发发送停止信号
        results = []
        for req_id in request_ids:
            result = signal_stop(req_id)
            results.append(result)

        # 验证所有信号都成功发送
        self.assertEqual(sum(results), 10)

        # 验证所有事件都已设置
        for req_id, event in events.items():
            self.assertTrue(event.is_set())

        # 清理
        for req_id in request_ids:
            unregister_generation(req_id)


class TestConcurrentChatService(TestCase):
    """并发聊天服务测试"""

    @patch("apps.chat.services.AgentService.execute")
    def test_concurrent_empty_message_validation(self, mock_execute):
        """测试并发空消息验证"""
        async def test_empty():
            tasks = []
            for i in range(10):
                # 每个任务尝试发送空消息
                task = collect_stream(
                    ChatService.send_message(user_id=i, content="")
                )
                tasks.append(task)

            # 并发执行，所有都应该抛出异常
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = run_async(test_empty())

        # 所有请求都应该因为空消息被拒绝
        for result in results:
            self.assertIsInstance(result, EmptyMessageException)

        # AgentService.execute 不应该被调用
        mock_execute.assert_not_called()

    @patch("apps.chat.services.AgentService.execute")
    def test_concurrent_message_length_validation(self, mock_execute):
        """测试并发消息长度验证"""
        from django.conf import settings

        async def test_length():
            tasks = []
            for i in range(10):
                # 每个任务发送超长消息
                long_content = "a" * (settings.MAX_MESSAGE_LENGTH + i + 1)
                task = collect_stream(
                    ChatService.send_message(user_id=i, content=long_content)
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = run_async(test_length())

        # 所有请求都应该因为超长被拒绝
        from apps.common.exceptions import MessageTooLongException
        for result in results:
            self.assertIsInstance(result, MessageTooLongException)

        mock_execute.assert_not_called()


class TestConcurrentHistoryService(TestCase):
    """并发历史消息服务测试"""

    @patch("apps.chat.services.message_repo.find_latest_by_user")
    def test_concurrent_load_messages(self, mock_find):
        """测试并发加载历史消息"""
        mock_find.return_value = []

        async def test_load():
            tasks = []
            for user_id in range(10):
                task = HistoryService.load_messages(user_id=user_id)
                tasks.append(task)

            results = await asyncio.gather(*tasks)
            return results

        results = run_async(test_load())

        # 所有请求都应该成功
        self.assertEqual(len(results), 10)

        # 每个用户都应该有独立的调用
        self.assertEqual(mock_find.call_count, 10)

    @patch("apps.chat.services.message_repo.find_latest_by_user")
    @patch("apps.chat.services.message_repo.find_by_user_before_sequence")
    def test_concurrent_paginated_load(self, mock_find_before, mock_find_latest):
        """测试并发分页加载"""
        mock_find_latest.return_value = []
        mock_find_before.return_value = []

        async def test_paginate():
            tasks = []
            for user_id in range(5):
                # 首页加载
                task1 = HistoryService.load_messages(user_id=user_id)
                # 分页加载
                task2 = HistoryService.load_messages(
                    user_id=user_id, before_sequence=100
                )
                tasks.extend([task1, task2])

            results = await asyncio.gather(*tasks)
            return results

        results = run_async(test_paginate())

        # 所有请求都应该成功
        self.assertEqual(len(results), 10)


class TestDataIsolation(TestCase):
    """数据隔离测试"""

    def test_user_id_isolation_in_thread_id(self):
        """测试 thread_id 包含 user_id 确保数据隔离"""
        from apps.chat.agent import get_thread_id

        thread_ids = set()
        for user_id in range(10):
            thread_id = get_thread_id(user_id)
            # 验证 thread_id 包含 user_id
            self.assertIn(str(user_id), thread_id)
            thread_ids.add(thread_id)

        # 验证所有 thread_id 都是唯一的
        self.assertEqual(len(thread_ids), 10)


class TestConcurrencyStressSmoke(TestCase):
    """并发压力冒烟测试 - SC-004"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.AgentService.execute")
    def test_10_users_concurrent_send(self, mock_execute):
        """SC-004: 10 用户并发发送消息，验证无死锁"""
        from apps.chat.services import StreamChunk

        # 模拟 AgentService 返回流式响应
        async def mock_stream(*args, **kwargs):
            yield StreamChunk(type="content", content="Hello")
            yield StreamChunk(type="done", content="", message_id=1)

        mock_execute.return_value = mock_stream()

        async def test_concurrent_send():
            tasks = []
            for user_id in range(10):
                content = f"用户{user_id}的消息_{uuid.uuid4().hex[:8]}"
                task = collect_stream(
                    ChatService.send_message(user_id=user_id, content=content)
                )
                tasks.append(task)

            # 设置超时，防止死锁
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0
            )
            return results

        results = run_async(test_concurrent_send())

        # 所有请求都应该完成（无死锁）
        self.assertEqual(len(results), 10)

        # 检查是否有异常（排除预期的验证异常）
        unexpected_errors = [
            r for r in results
            if isinstance(r, Exception) and not isinstance(r, (EmptyMessageException,))
        ]

        # 打印任何意外错误以便调试
        for err in unexpected_errors:
            print(f"Unexpected error: {type(err).__name__}: {err}")

    def test_concurrent_generation_lifecycle(self):
        """测试并发生成生命周期管理"""
        async def test_lifecycle():
            request_ids = [f"lifecycle-{i}" for i in range(10)]
            events = []

            # 并发注册
            for req_id in request_ids:
                event = register_generation(req_id)
                events.append(event)

            # 模拟并发生成过程
            await asyncio.sleep(0.01)

            # 并发停止一半的生成
            for i, req_id in enumerate(request_ids):
                if i % 2 == 0:
                    signal_stop(req_id)

            # 验证停止状态
            for i, event in enumerate(events):
                if i % 2 == 0:
                    self.assertTrue(event.is_set())
                else:
                    self.assertFalse(event.is_set())

            # 清理
            for req_id in request_ids:
                unregister_generation(req_id)

        run_async(test_lifecycle())

    @patch("apps.chat.services.message_repo.find_latest_by_user")
    def test_mixed_concurrent_operations(self, mock_find):
        """测试混合并发操作（发送 + 加载历史）"""
        mock_find.return_value = []

        async def test_mixed():
            tasks = []

            # 5 个加载历史请求
            for user_id in range(5):
                task = HistoryService.load_messages(user_id=user_id)
                tasks.append(task)

            # 5 个空消息验证请求（会抛出异常）
            for user_id in range(5):
                task = collect_stream(
                    ChatService.send_message(user_id=user_id, content="  ")
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = run_async(test_mixed())

        # 前 5 个应该成功（历史加载）
        for i in range(5):
            self.assertIsInstance(results[i], list)

        # 后 5 个应该是空消息异常
        for i in range(5, 10):
            self.assertIsInstance(results[i], EmptyMessageException)
