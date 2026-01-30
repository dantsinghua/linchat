"""
活跃生成管理 + LLM 异常映射

管理正在进行的流式生成会话，以及将原始异常映射为标准 LLM 异常。
"""

import asyncio
from typing import Optional

from apps.common.exceptions import (
    LLMConnectionError,
    LLMContentFilterError,
    LLMException,
    LLMInvalidResponseError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# 存储正在生成中的会话，用于停止生成
# key: request_id, value: asyncio.Event (设置时表示应该停止)
_active_generations: dict[str, asyncio.Event] = {}


def register_generation(request_id: str) -> asyncio.Event:
    """注册一个新的生成会话"""
    stop_event = asyncio.Event()
    _active_generations[request_id] = stop_event
    return stop_event


def unregister_generation(request_id: str) -> None:
    """取消注册生成会话"""
    _active_generations.pop(request_id, None)


def get_stop_event(request_id: str) -> Optional[asyncio.Event]:
    """获取停止事件"""
    return _active_generations.get(request_id)


def signal_stop(request_id: str) -> bool:
    """发送停止信号"""
    stop_event = _active_generations.get(request_id)
    if stop_event:
        stop_event.set()
        return True
    return False


def map_llm_exception(e: Exception) -> LLMException:
    """
    将原始异常映射为 LLM 异常

    参考: rule-model.md#R_LLM_RETRY_001
    """
    error_str = str(e).lower()

    if any(kw in error_str for kw in ["connection", "connect", "network", "unreachable"]):
        return LLMConnectionError()

    if any(kw in error_str for kw in ["timeout", "timed out"]):
        return LLMTimeoutError()

    if any(kw in error_str for kw in ["rate limit", "too many requests", "429"]):
        return LLMRateLimitError()

    if any(kw in error_str for kw in ["content filter", "content policy", "moderation"]):
        return LLMContentFilterError()

    if any(kw in error_str for kw in ["quota", "insufficient", "billing"]):
        return LLMQuotaExceededError()

    return LLMInvalidResponseError(str(e))
