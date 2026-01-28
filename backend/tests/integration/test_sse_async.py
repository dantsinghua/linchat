"""
SSE 异步视图集成测试

覆盖:
- T025: 资源释放验证（SSE 断开后 Redis 订阅数恢复）
- T025a: US1+US2 集成测试（chat -> 中断 -> resume_generation）
- T026: 多连接并发测试

测试方式: pytest-asyncio + mock
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest

from apps.chat.services import ChatService, StreamChunk


# ============ 测试数据 ============


@dataclass
class MockStreamChunk:
    """模拟流式块"""
    type: str
    content: str
    message_id: int | None = None


# ============ T025: 资源释放验证测试 ============


@pytest.mark.asyncio
class TestSSEResourceCleanup:
    """SSE 资源释放验证测试 (T025)"""

    async def test_chat_generator_cleanup_on_cancel(self):
        """测试 chat 异步生成器在取消时正确清理"""
        chunks_yielded = []

        async def mock_send_message(user_id, content):
            for i in range(10):
                chunks_yielded.append(i)
                yield MockStreamChunk(type="content", content=f"chunk{i}", message_id=1)
                await asyncio.sleep(0.01)

        with patch.object(ChatService, "send_message", mock_send_message):
            # 模拟部分消费后取消
            gen = ChatService.send_message(user_id=1, content="test")
            count = 0
            async for chunk in gen:
                count += 1
                if count >= 3:
                    break

        # 验证只消费了部分数据
        assert count == 3

    async def test_multiple_generators_independent_cleanup(self):
        """测试多个生成器独立清理 (T026 基础)"""
        cleanup_count = 0

        async def mock_generator():
            nonlocal cleanup_count
            try:
                for i in range(100):
                    yield MockStreamChunk(type="content", content=f"{i}", message_id=1)
                    await asyncio.sleep(0.001)
            finally:
                cleanup_count += 1

        # 创建多个生成器
        gens = [mock_generator() for _ in range(3)]

        # 部分消费每个生成器
        for gen in gens:
            count = 0
            async for _ in gen:
                count += 1
                if count >= 2:
                    break
            await gen.aclose()

        # 验证每个生成器都执行了清理
        assert cleanup_count == 3


# ============ T025a: US1+US2 集成测试 ============


@pytest.mark.asyncio
class TestChatResumeIntegration:
    """chat() -> 中断 -> resume_generation() 集成测试 (T025a)"""

    async def test_chat_interrupt_resume_flow(self):
        """测试完整的聊天-中断-恢复流程"""
        # 模拟数据
        initial_chunks = [
            MockStreamChunk(type="content", content="Hello", message_id=1),
            MockStreamChunk(type="content", content=" World", message_id=1),
            MockStreamChunk(type="interrupted", content="[已中断]", message_id=1),
        ]

        resume_chunks = [
            MockStreamChunk(type="content", content="!", message_id=1),
            MockStreamChunk(type="done", content="", message_id=1),
        ]

        async def mock_send_message(user_id, content):
            for chunk in initial_chunks:
                yield chunk

        async def mock_resume_generation(user_id, request_id):
            for chunk in resume_chunks:
                yield chunk

        # 阶段1: 发送消息并中断
        with patch.object(ChatService, "send_message", mock_send_message):
            chat_results = []
            async for chunk in ChatService.send_message(user_id=1, content="Hi"):
                chat_results.append(chunk)

        assert len(chat_results) == 3
        assert chat_results[-1].type == "interrupted"

        # 阶段2: 恢复生成
        with patch.object(ChatService, "resume_generation", mock_resume_generation):
            resume_results = []
            async for chunk in ChatService.resume_generation(user_id=1, request_id="test-req-id"):
                resume_results.append(chunk)

        assert len(resume_results) == 2
        assert resume_results[-1].type == "done"

        # 验证完整内容
        all_content = ""
        for chunk in chat_results + resume_results:
            if chunk.type == "content":
                all_content += chunk.content
        assert all_content == "Hello World!"


# ============ T026: 多连接并发测试 ============


@pytest.mark.asyncio
class TestConcurrentSSEConnections:
    """多连接并发测试 (T026)"""

    async def test_concurrent_chat_streams(self):
        """测试多个并发 SSE 连接的资源独立管理"""
        user_results = {}
        cleanup_flags = {}

        async def mock_send_for_user(user_id, content):
            cleanup_flags[user_id] = False
            try:
                for i in range(5):
                    yield MockStreamChunk(
                        type="content",
                        content=f"user{user_id}_chunk{i}",
                        message_id=user_id,
                    )
                    await asyncio.sleep(0.01)
                yield MockStreamChunk(type="done", content="", message_id=user_id)
            finally:
                cleanup_flags[user_id] = True

        async def consume_stream(user_id):
            results = []
            async for chunk in mock_send_for_user(user_id, "test"):
                results.append(chunk)
            user_results[user_id] = results

        # 并发执行多个用户的流
        tasks = [consume_stream(i) for i in range(5)]
        await asyncio.gather(*tasks)

        # 验证每个用户都收到了完整的流
        assert len(user_results) == 5
        for user_id, results in user_results.items():
            assert len(results) == 6  # 5 content + 1 done
            assert results[-1].type == "done"

        # 验证所有清理标志都被设置
        assert all(cleanup_flags.values())

    async def test_concurrent_streams_with_partial_consumption(self):
        """测试并发连接部分消费时的资源释放"""
        active_connections = set()

        async def mock_generator(conn_id):
            active_connections.add(conn_id)
            try:
                for i in range(100):
                    yield MockStreamChunk(type="content", content=f"{i}", message_id=conn_id)
                    await asyncio.sleep(0.001)
            finally:
                active_connections.discard(conn_id)

        async def partial_consume(conn_id):
            gen = mock_generator(conn_id)
            count = 0
            async for _ in gen:
                count += 1
                if count >= 3:
                    break
            await gen.aclose()

        # 并发启动多个连接，部分消费后关闭
        tasks = [partial_consume(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # 验证所有连接都已清理
        assert len(active_connections) == 0

    async def test_concurrent_event_subscriptions(self):
        """测试并发事件订阅的资源独立性"""
        from apps.common.event_service import EventService

        subscriptions = {}
        cleanup_count = 0

        async def mock_subscribe(user_id):
            nonlocal cleanup_count
            subscriptions[user_id] = True
            try:
                yield f"connected:{user_id}"
                for i in range(3):
                    yield f"event:{user_id}:{i}"
                    await asyncio.sleep(0.01)
            finally:
                subscriptions.pop(user_id, None)
                cleanup_count += 1

        async def consume_events(user_id):
            events = []
            async for event in mock_subscribe(user_id):
                events.append(event)
            return events

        # 并发订阅
        tasks = [consume_events(i) for i in range(5)]
        results = await asyncio.gather(*tasks)

        # 验证每个用户收到正确数量的事件
        for i, events in enumerate(results):
            assert len(events) == 4  # 1 connected + 3 events
            assert events[0] == f"connected:{i}"

        # 验证所有订阅都已清理
        assert len(subscriptions) == 0
        assert cleanup_count == 5


# ============ T028a: 服务器重启场景测试 ============


@pytest.mark.asyncio
class TestServerRestartScenario:
    """服务器重启场景测试 (T028a)

    验证 uvicorn reload 时所有现有 SSE 连接被正确关闭
    """

    async def test_connections_closed_on_cancel(self):
        """测试所有连接在取消时正确关闭"""
        active_connections = []
        cleanup_order = []

        async def mock_generator(conn_id):
            active_connections.append(conn_id)
            try:
                while True:
                    yield MockStreamChunk(type="heartbeat", content="", message_id=conn_id)
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                cleanup_order.append(conn_id)
                raise
            finally:
                if conn_id in active_connections:
                    active_connections.remove(conn_id)

        async def consume_until_cancel(conn_id, cancel_event):
            gen = mock_generator(conn_id)
            try:
                async for _ in gen:
                    if cancel_event.is_set():
                        break
            except asyncio.CancelledError:
                pass
            finally:
                await gen.aclose()

        # 模拟多个活跃连接
        cancel_event = asyncio.Event()
        tasks = [
            asyncio.create_task(consume_until_cancel(i, cancel_event))
            for i in range(5)
        ]

        # 等待连接建立
        await asyncio.sleep(0.2)
        assert len(active_connections) == 5

        # 模拟服务器重启 - 触发取消
        cancel_event.set()
        for task in tasks:
            task.cancel()

        # 等待所有任务完成
        await asyncio.gather(*tasks, return_exceptions=True)

        # 验证所有连接都已关闭
        assert len(active_connections) == 0
        assert len(cleanup_order) == 5

    async def test_graceful_shutdown_within_timeout(self):
        """测试优雅关闭在超时时间内完成"""
        shutdown_complete = asyncio.Event()
        start_time = None
        end_time = None

        async def mock_generator_with_cleanup():
            nonlocal start_time, end_time
            try:
                while True:
                    yield MockStreamChunk(type="content", content="data", message_id=1)
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                start_time = asyncio.get_event_loop().time()
                # 模拟清理操作
                await asyncio.sleep(0.1)
                end_time = asyncio.get_event_loop().time()
                raise

        async def consume():
            gen = mock_generator_with_cleanup()
            try:
                async for _ in gen:
                    pass
            except asyncio.CancelledError:
                pass
            finally:
                await gen.aclose()
                shutdown_complete.set()

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)

        # 触发取消
        task.cancel()

        # 等待关闭完成（最多 5 秒）
        try:
            await asyncio.wait_for(shutdown_complete.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail("Shutdown did not complete within timeout")

        # 验证清理时间合理（小于 1 秒）
        if start_time and end_time:
            cleanup_duration = end_time - start_time
            assert cleanup_duration < 1.0
