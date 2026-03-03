import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from django.conf import settings

logger = logging.getLogger(__name__)

_LOCK_KEY = "multimodal:gpu_lock"
_LOCK_TTL = 60
_HEARTBEAT_INTERVAL = 30
_POLL_INTERVAL = 3


class GPULockTimeout(Exception):
    pass


def _decode(val) -> str:
    return val.decode("utf-8") if isinstance(val, bytes) else str(val)


@asynccontextmanager
async def acquire_gpu_lock(request_id: str) -> AsyncGenerator[None, None]:
    from core.redis import get_redis

    max_wait = getattr(settings, "GPU_LOCK_MAX_WAIT", 600)
    client = await get_redis()
    acquired, reentrant, heartbeat_task = False, False, None

    try:
        elapsed = 0
        while elapsed < max_wait:
            acquired = await client.set(_LOCK_KEY, request_id, nx=True, ex=_LOCK_TTL)
            if acquired:
                break
            current = await client.get(_LOCK_KEY)
            if current is not None and _decode(current) == request_id:
                reentrant = True
                break
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

        if not acquired and not reentrant:
            raise GPULockTimeout(f"等待 GPU 锁超时（{max_wait}秒）: request_id={request_id}")
        if acquired:
            heartbeat_task = asyncio.create_task(_heartbeat(client, request_id))
        logger.info("GPU 锁已获取: request_id=%s, reentrant=%s", request_id, reentrant)
        yield
    finally:
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if acquired and not reentrant:
            try:
                current = await client.get(_LOCK_KEY)
                if current is not None and _decode(current) == request_id:
                    await client.delete(_LOCK_KEY)
                    logger.info("GPU 锁已释放: request_id=%s", request_id)
            except Exception as e:
                logger.warning("释放 GPU 锁失败: request_id=%s, error=%s", request_id, e)


async def _heartbeat(client, request_id: str) -> None:
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            current = await client.get(_LOCK_KEY)
            if current is None or _decode(current) != request_id:
                break
            await client.expire(_LOCK_KEY, _LOCK_TTL)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("GPU 锁心跳异常: request_id=%s, error=%s", request_id, e)
