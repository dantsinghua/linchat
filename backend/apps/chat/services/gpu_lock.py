"""GPU 全局互斥锁

通过 Redis 分布式锁保证同一时刻只有一个多模态/文档解析请求占用 GPU。
锁支持可重入（同一 request_id 可多次获取）、心跳续期和超时等待。

Redis 键: multimodal:gpu_lock
值: request_id（用于可重入判断）
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from django.conf import settings

logger = logging.getLogger(__name__)

# 锁配置默认值
_LOCK_KEY = "multimodal:gpu_lock"
_LOCK_TTL = 60  # 锁 TTL（秒），心跳续期防止崩溃后长时间锁死
_HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
_POLL_INTERVAL = 3  # 等待锁的轮询间隔（秒）


class GPULockTimeout(Exception):
    """等待 GPU 锁超时"""

    pass


@asynccontextmanager
async def acquire_gpu_lock(request_id: str) -> AsyncGenerator[None, None]:
    """获取 GPU 全局互斥锁（异步上下文管理器）

    Args:
        request_id: 请求 ID，用于可重入判断

    Raises:
        GPULockTimeout: 等待超时

    Usage:
        async with acquire_gpu_lock(request_id):
            # GPU 独占操作
            ...
    """
    from core.redis import get_redis

    max_wait = getattr(settings, "GPU_LOCK_MAX_WAIT", 600)
    client = await get_redis()
    acquired = False
    reentrant = False
    heartbeat_task = None

    try:
        # 尝试获取锁
        elapsed = 0
        while elapsed < max_wait:
            # SETNX + TTL 原子操作
            acquired = await client.set(
                _LOCK_KEY, request_id, nx=True, ex=_LOCK_TTL
            )
            if acquired:
                break

            # 检查可重入：当前锁持有者是否为同一 request_id
            current_holder = await client.get(_LOCK_KEY)
            if current_holder is not None:
                holder_str = (
                    current_holder.decode("utf-8")
                    if isinstance(current_holder, bytes)
                    else str(current_holder)
                )
                if holder_str == request_id:
                    reentrant = True
                    break

            logger.debug(
                "GPU 锁被占用，等待中: request_id=%s, elapsed=%ds, holder=%s",
                request_id,
                elapsed,
                current_holder,
            )
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

        if not acquired and not reentrant:
            raise GPULockTimeout(
                f"等待 GPU 锁超时（{max_wait}秒）: request_id={request_id}"
            )

        # 启动心跳续期任务（仅非重入时）
        if acquired:
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(client, request_id)
            )

        logger.info(
            "GPU 锁已获取: request_id=%s, reentrant=%s",
            request_id,
            reentrant,
        )
        yield

    finally:
        # 停止心跳
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # 释放锁（仅释放自己持有的，防止误删他人锁）
        if acquired and not reentrant:
            try:
                current = await client.get(_LOCK_KEY)
                if current is not None:
                    holder = (
                        current.decode("utf-8")
                        if isinstance(current, bytes)
                        else str(current)
                    )
                    if holder == request_id:
                        await client.delete(_LOCK_KEY)
                        logger.info("GPU 锁已释放: request_id=%s", request_id)
            except Exception as e:
                logger.warning("释放 GPU 锁失败: request_id=%s, error=%s", request_id, e)


async def _heartbeat_loop(client, request_id: str) -> None:
    """心跳续期循环，每 30 秒刷新锁 TTL"""
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            # 仅续期自己持有的锁
            current = await client.get(_LOCK_KEY)
            if current is not None:
                holder = (
                    current.decode("utf-8")
                    if isinstance(current, bytes)
                    else str(current)
                )
                if holder == request_id:
                    await client.expire(_LOCK_KEY, _LOCK_TTL)
                    logger.debug("GPU 锁心跳续期: request_id=%s", request_id)
                else:
                    break  # 锁已被他人持有，停止续期
            else:
                break  # 锁不存在，停止续期
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("GPU 锁心跳异常: request_id=%s, error=%s", request_id, e)
