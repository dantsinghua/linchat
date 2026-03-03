import logging

logger = logging.getLogger(__name__)


async def check_rate_limit(key: str, limit: int, window: int = 60) -> tuple[bool, int]:
    """Redis INCR 速率限制。返回 (allowed, current_count)。"""
    from core.redis import get_redis
    redis = await get_redis()
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count <= limit, count
    except Exception:
        logger.warning(f"速率限制检查失败: {key}", exc_info=True)
        return True, 0
