"""
core/redis.py 共享连接池测试（batch-11）

覆盖：
- 同一事件循环内 get_redis() 复用同一 BlockingConnectionPool
- 池按事件循环隔离，loop 回收后 WeakKeyDictionary 自动清理
- .aclose() 不销毁共享池（auto_close_connection_pool=False）
- max_connections / timeout / decode_responses 由 settings 与池参数决定
- get_async_redis_client 别名同样绑定共享池

不依赖真实 Redis 网络：BlockingConnectionPool 惰性建连，仅构造池对象。
"""
import asyncio
import gc

import pytest
import redis.asyncio as aioredis
from django.test import override_settings

from core.redis import (
    _POOLS,
    RedisClient,
    _get_pool,
    get_async_redis_client,
    get_redis,
)


@pytest.fixture(autouse=True)
def _clear_pools():
    """每个用例前后清空共享池缓存，避免跨用例污染。"""
    _POOLS.clear()
    yield
    _POOLS.clear()


@pytest.mark.asyncio
async def test_get_redis_reuses_pool_same_loop():
    """同一 loop 内两次 get_redis() 共享同一连接池对象。"""
    client_a = await get_redis()
    client_b = await get_redis()
    assert client_a.connection_pool is client_b.connection_pool
    assert isinstance(client_a.connection_pool, aioredis.BlockingConnectionPool)


def test_pool_isolated_per_event_loop():
    """不同事件循环各自建池；loop 回收后 _POOLS 条目被 GC 清理。"""
    _POOLS.clear()

    async def _grab() -> int:
        return id(_get_pool())

    id1 = asyncio.run(_grab())
    gc.collect()
    id2 = asyncio.run(_grab())

    assert id1 != id2  # 两个独立 loop -> 两个独立池
    gc.collect()
    assert len(_POOLS) == 0  # 两个临时 loop 关闭后弱引用条目被回收


@pytest.mark.asyncio
async def test_aclose_does_not_disconnect_shared_pool():
    """.aclose() 只归还连接、不销毁共享池；后续 get_redis 仍取到同一存活池。"""
    client = await get_redis()
    pool_before = client.connection_pool
    assert client.auto_close_connection_pool is False

    await client.aclose()

    client2 = await get_redis()
    assert client2.connection_pool is pool_before


@pytest.mark.asyncio
async def test_max_connections_from_settings():
    """max_connections 取自 settings.REDIS_MAX_CONNECTIONS。"""
    with override_settings(REDIS_MAX_CONNECTIONS=7):
        _POOLS.clear()
        pool = _get_pool()
        assert pool.max_connections == 7


@pytest.mark.asyncio
async def test_pool_uses_configured_timeout_and_decode():
    """池按预期配置：阻塞获取超时=10s、decode_responses=True。"""
    pool = _get_pool()
    assert pool.timeout == 10
    assert pool.connection_kwargs.get("decode_responses") is True


@pytest.mark.asyncio
async def test_get_async_redis_client_alias_uses_pool():
    """别名 get_async_redis_client 返回的 client 同样绑定共享池。"""
    alias_client = await get_async_redis_client()
    direct_client = await RedisClient.get_client()
    assert alias_client.connection_pool is direct_client.connection_pool
