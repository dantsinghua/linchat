"""
Redis 连接管理模块

参考: data-model.md#三、Redis缓存设计
"""
import json
from datetime import timedelta
from typing import Any

import redis.asyncio as aioredis
from django.conf import settings


class RedisClient:
    """Redis 客户端封装

    注意：在 WSGI 环境下（Django runserver）使用异步视图时，
    每次请求可能使用不同的事件循环，因此每次调用创建新连接。
    """

    @staticmethod
    async def get_client() -> aioredis.Redis:
        """获取 Redis 客户端连接

        每次调用创建新连接，避免事件循环问题
        """
        return aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )


# ============ 便捷方法 ============

async def get_redis() -> aioredis.Redis:
    """获取 Redis 客户端"""
    return await RedisClient.get_client()


async def redis_get(key: str) -> str | None:
    """获取字符串值"""
    client = await get_redis()
    return await client.get(key)


async def redis_set(
    key: str,
    value: str,
    ex: int | None = None,
) -> bool:
    """设置字符串值"""
    client = await get_redis()
    return await client.set(key, value, ex=ex)


async def redis_setex(key: str, seconds: int, value: str) -> bool:
    """设置带过期时间的字符串值"""
    client = await get_redis()
    return await client.setex(key, seconds, value)


async def redis_delete(key: str) -> int:
    """删除键"""
    client = await get_redis()
    return await client.delete(key)


async def redis_expire(key: str, seconds: int) -> bool:
    """设置键的过期时间"""
    client = await get_redis()
    return await client.expire(key, seconds)


async def redis_ttl(key: str) -> int:
    """获取键的剩余过期时间（秒）"""
    client = await get_redis()
    return await client.ttl(key)


async def redis_exists(key: str) -> bool:
    """检查键是否存在"""
    client = await get_redis()
    return await client.exists(key) > 0


# ============ JSON 操作封装 ============

async def redis_get_json(key: str) -> dict | list | None:
    """获取 JSON 值"""
    value = await redis_get(key)
    if value:
        return json.loads(value)
    return None


async def redis_set_json(
    key: str,
    value: dict | list,
    ex: int | None = None,
) -> bool:
    """设置 JSON 值"""
    return await redis_set(key, json.dumps(value, ensure_ascii=False), ex=ex)


async def redis_setex_json(key: str, seconds: int, value: dict | list) -> bool:
    """设置带过期时间的 JSON 值"""
    return await redis_setex(key, seconds, json.dumps(value, ensure_ascii=False))


# ============ Token 相关键名 ============

def get_token_key(token_hash: str) -> str:
    """获取 Token 缓存键名

    参考: data-model.md#3.1 认证相关
    """
    return f"auth:token:{token_hash}"


def get_user_token_key(user_id: int) -> str:
    """获取用户当前 Token 索引键名（用于单点登录）

    参考: data-model.md#3.1 单点登录Token索引
    """
    return f"auth:user_token:{user_id}"


def get_captcha_key(captcha_id: str) -> str:
    """获取验证码缓存键名

    参考: data-model.md#3.1 验证码缓存
    """
    return f"auth:captcha:{captcha_id}"


def get_login_fail_key(username: str) -> str:
    """获取登录失败计数键名

    参考: data-model.md#3.1 登录失败计数
    """
    return f"auth:fail:{username}"


# ============ Pub/Sub 相关 ============

def get_user_events_channel(user_id: int) -> str:
    """获取用户事件订阅频道名（用于 SSE 推送）

    参考: process-model.md#一点五、单点登录SSE推送流程
    """
    return f"events:user:{user_id}"
