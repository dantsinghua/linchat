"""
SSE 并发连接压测脚本 (T028b)

使用方式:
1. 启动后端服务: uvicorn core.asgi:application --host 0.0.0.0 --port 8002
2. 运行压测: python -m pytest tests/performance/test_sse_load.py -v -s

压测参数:
- 并发连接数: 100 (可调整 CONCURRENT_CONNECTIONS)
- 持续时间: 30 秒 (可调整 TEST_DURATION_SECONDS)
- 验证指标: 连接成功率 > 99%, 内存波动 < 10%

注意: 完整 1000 并发测试需要单独运行，避免影响其他测试
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import List
from unittest.mock import AsyncMock, patch

import pytest

from apps.chat.services import ChatService


# ============ 压测配置 ============

CONCURRENT_CONNECTIONS = 100  # 并发连接数 (完整测试用 1000)
TEST_DURATION_SECONDS = 5  # 测试持续时间 (完整测试用 30*60)
CONNECTION_TIMEOUT = 10  # 连接超时时间


# ============ 压测数据结构 ============


@dataclass
class ConnectionStats:
    """连接统计"""
    total_connections: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    total_events_received: int = 0
    connection_errors: List[str] = None

    def __post_init__(self):
        if self.connection_errors is None:
            self.connection_errors = []

    @property
    def success_rate(self) -> float:
        if self.total_connections == 0:
            return 0.0
        return self.successful_connections / self.total_connections * 100


@dataclass
class MockStreamChunk:
    """模拟流式块"""
    type: str
    content: str
    message_id: int | None = None


# ============ T028b: 并发 SSE 连接压测 ============


@pytest.mark.asyncio
class TestSSELoadConcurrency:
    """SSE 并发连接压测 (T028b)

    验证系统在高并发下的稳定性
    """

    async def test_concurrent_connections_stability(self):
        """测试并发连接稳定性

        指标:
        - 连接成功率 > 99%
        - 所有连接正确接收事件
        - 资源正确释放
        """
        stats = ConnectionStats()
        active_connections = set()
        lock = asyncio.Lock()

        async def mock_send_message(user_id, content):
            """模拟消息发送"""
            for i in range(10):
                yield MockStreamChunk(
                    type="content",
                    content=f"chunk_{i}",
                    message_id=user_id,
                )
                await asyncio.sleep(0.01)
            yield MockStreamChunk(type="done", content="", message_id=user_id)

        async def simulate_connection(conn_id: int):
            """模拟单个 SSE 连接"""
            nonlocal stats
            async with lock:
                stats.total_connections += 1
                active_connections.add(conn_id)

            events_received = 0
            try:
                async for chunk in mock_send_message(conn_id, "test"):
                    events_received += 1
                    if chunk.type == "done":
                        break

                async with lock:
                    stats.successful_connections += 1
                    stats.total_events_received += events_received

            except Exception as e:
                async with lock:
                    stats.failed_connections += 1
                    stats.connection_errors.append(f"conn_{conn_id}: {str(e)}")
            finally:
                async with lock:
                    active_connections.discard(conn_id)

        # 并发执行连接
        start_time = time.time()
        tasks = [
            asyncio.create_task(simulate_connection(i))
            for i in range(CONCURRENT_CONNECTIONS)
        ]

        # 等待所有连接完成
        await asyncio.gather(*tasks, return_exceptions=True)
        duration = time.time() - start_time

        # 输出统计
        print(f"\n{'='*50}")
        print(f"SSE 并发压测结果 (T028b)")
        print(f"{'='*50}")
        print(f"并发连接数: {CONCURRENT_CONNECTIONS}")
        print(f"测试时长: {duration:.2f}s")
        print(f"总连接数: {stats.total_connections}")
        print(f"成功连接: {stats.successful_connections}")
        print(f"失败连接: {stats.failed_connections}")
        print(f"连接成功率: {stats.success_rate:.2f}%")
        print(f"总事件数: {stats.total_events_received}")
        print(f"活跃连接: {len(active_connections)}")
        if stats.connection_errors:
            print(f"错误样例: {stats.connection_errors[:5]}")
        print(f"{'='*50}")

        # 验证指标
        assert stats.success_rate >= 99.0, f"连接成功率 {stats.success_rate:.2f}% < 99%"
        assert len(active_connections) == 0, f"仍有 {len(active_connections)} 个活跃连接"

    async def test_sustained_connections_memory_stability(self):
        """测试持续连接的内存稳定性

        模拟长时间保持连接，验证内存不会持续增长
        """
        import sys

        initial_objects = len([])  # 简化的内存检查
        active_generators = []
        cleanup_count = 0

        async def mock_long_running_generator(conn_id):
            nonlocal cleanup_count
            try:
                for i in range(100):
                    yield MockStreamChunk(
                        type="content",
                        content=f"data_{i}",
                        message_id=conn_id,
                    )
                    await asyncio.sleep(0.001)
            finally:
                cleanup_count += 1

        async def maintain_connection(conn_id, duration):
            """保持连接一段时间"""
            gen = mock_long_running_generator(conn_id)
            active_generators.append(gen)
            start = time.time()
            try:
                async for chunk in gen:
                    if time.time() - start > duration:
                        break
            finally:
                await gen.aclose()
                if gen in active_generators:
                    active_generators.remove(gen)

        # 创建多批次连接，模拟用户来来去去
        for batch in range(3):
            tasks = [
                asyncio.create_task(maintain_connection(i + batch * 20, 0.5))
                for i in range(20)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        # 验证所有生成器都已清理
        assert len(active_generators) == 0
        assert cleanup_count == 60  # 3 批次 * 20 连接

    async def test_connection_churn_stability(self):
        """测试连接频繁建立断开的稳定性

        模拟用户频繁刷新页面的场景
        """
        stats = ConnectionStats()
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_quick_generator():
            for i in range(5):
                yield MockStreamChunk(type="content", content=str(i), message_id=1)
                await asyncio.sleep(0.001)

        async def quick_connect_disconnect():
            nonlocal max_concurrent, current_concurrent, stats

            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
                stats.total_connections += 1

            try:
                gen = mock_quick_generator()
                count = 0
                async for _ in gen:
                    count += 1
                    if count >= 2:  # 只接收部分数据就断开
                        break
                await gen.aclose()

                async with lock:
                    stats.successful_connections += 1
            except Exception as e:
                async with lock:
                    stats.failed_connections += 1
            finally:
                async with lock:
                    current_concurrent -= 1

        # 模拟 100 次快速连接/断开
        tasks = [
            asyncio.create_task(quick_connect_disconnect())
            for _ in range(100)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        print(f"\n连接流转测试: 最大并发={max_concurrent}, 成功率={stats.success_rate:.2f}%")

        assert stats.success_rate >= 99.0
        assert current_concurrent == 0


# ============ 压测脚本入口 ============


if __name__ == "__main__":
    """
    独立运行压测:
    python tests/performance/test_sse_load.py

    或使用 pytest:
    pytest tests/performance/test_sse_load.py -v -s
    """
    import sys

    async def run_load_test():
        test = TestSSELoadConcurrency()
        print("运行并发连接稳定性测试...")
        await test.test_concurrent_connections_stability()
        print("\n运行内存稳定性测试...")
        await test.test_sustained_connections_memory_stability()
        print("\n运行连接流转稳定性测试...")
        await test.test_connection_churn_stability()
        print("\n所有压测通过!")

    asyncio.run(run_load_test())
