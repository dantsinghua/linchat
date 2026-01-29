"""
LangGraph Agent 定义

参考:
- data-model.md#五、LangGraph RedisSaver 配置
- behavior-model.md#2.2 执行LangGraph Agent（B_CHAT_002）
- rule-model.md#R_AGENT_001 Agent执行超时规则
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.prebuilt import create_react_agent

from apps.models.services import model_service

logger = logging.getLogger(__name__)


# ============ Checkpointer 工厂 ============


def _get_ttl_config() -> dict:
    """获取 TTL 配置"""
    return {
        "default_ttl": settings.LANGGRAPH_CHECKPOINT_TTL,  # 24小时（分钟）
        "refresh_on_read": settings.LANGGRAPH_CHECKPOINT_REFRESH_ON_READ,
    }


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[AsyncRedisSaver]:
    """
    获取 AsyncRedisSaver 实例（异步上下文管理器）

    由于 Django 使用线程模式处理 SSE 请求，每个请求在不同的事件循环中运行，
    因此不能缓存 AsyncRedisSaver 单例（它会绑定到创建时的事件循环）。

    使用方式:
        async with get_checkpointer() as checkpointer:
            agent = create_react_agent(model=llm, tools=[], checkpointer=checkpointer)
            # ... 使用 agent

    参考: data-model.md#五、LangGraph RedisSaver 配置
    要求: Redis Stack (包含 RediSearch 模块)

    Yields:
        AsyncRedisSaver: checkpointer 实例
    """
    async with AsyncRedisSaver.from_conn_string(
        redis_url=settings.REDIS_URL,
        ttl=_get_ttl_config(),
    ) as checkpointer:
        logger.debug("AsyncRedisSaver created for current event loop")
        yield checkpointer


# ============ Thread ID 约定 ============


def get_thread_id(user_id: int) -> str:
    """
    生成 thread_id

    参考: data-model.md#3.2 thread_id格式

    Args:
        user_id: 用户ID

    Returns:
        str: thread_id，格式为 "user_{user_id}"
    """
    return f"user_{user_id}"


# ============ LLM 配置 ============


async def get_llm() -> ChatOpenAI:
    """
    获取 LLM 实例（每次从数据库读取最新配置，确保修改即时生效）

    参考:
    - rule-model.md#R_LLM_RETRY_001 LLM重试策略规则
    - spec.md FR-010 配置修改即时生效
    - spec.md FR-007 NULL 参数不传递，非 NULL 参数传递
    - spec.md FR-014 后端内部获取激活模型配置

    Returns:
        ChatOpenAI: LLM 实例
    """
    config = await sync_to_async(model_service.get_active_model)("language")
    if not config:
        raise RuntimeError("未找到激活的语言模型配置，请在模型配置页面设置")

    # 构建参数：仅传入非 NULL 的选填参数（FR-007）
    kwargs: dict = {
        "base_url": config["url"],
        "api_key": config["api_key"] or "not-needed",
        "model": config["name"],
        "streaming": True,
        "stream_usage": True,
        "timeout": settings.LLM_CALL_TIMEOUT,
        "max_retries": settings.LLM_MAX_RETRIES,
    }

    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]
    if config.get("top_p") is not None:
        kwargs["top_p"] = config["top_p"]
    if config.get("frequency_penalty") is not None:
        kwargs["frequency_penalty"] = config["frequency_penalty"]
    if config.get("presence_penalty") is not None:
        kwargs["presence_penalty"] = config["presence_penalty"]

    return ChatOpenAI(**kwargs)


# ============ Agent 创建 ============


@asynccontextmanager
async def create_chat_agent():
    """
    创建聊天 Agent（异步上下文管理器）

    由于 checkpointer 是上下文管理器，agent 也需要通过上下文管理器方式使用，
    确保 checkpointer 在使用期间保持打开状态。

    使用方式:
        async with create_chat_agent() as agent:
            async for event in agent.astream_events(...):
                ...

    参考: behavior-model.md#2.2 执行LangGraph Agent

    Yields:
        CompiledGraph: 编译后的 Agent 图
    """
    async with get_checkpointer() as checkpointer:
        llm = await get_llm()

        # 创建 ReAct Agent（当前版本不使用工具）
        # 参考: behavior-model.md#2.2 - 使用 create_react_agent
        agent = create_react_agent(
            model=llm,
            tools=[],  # 当前版本不使用工具
            checkpointer=checkpointer,
        )

        yield agent


# ============ Agent 配置辅助 ============


def get_agent_config(user_id: int, callbacks: Optional[list] = None) -> dict:
    """
    获取 Agent 运行配置

    Args:
        user_id: 用户ID
        callbacks: 可选的回调列表（如 Langfuse handler）

    Returns:
        dict: Agent 配置字典
    """
    config = {
        "configurable": {
            "thread_id": get_thread_id(user_id),
        }
    }
    if callbacks:
        config["callbacks"] = callbacks
    return config
