"""SubAgent 工厂函数和公共工具管理

提供 run_subagent() 工厂函数（创建 react agent + 超时包裹）
和 get_common_tools() 公共工具列表。
"""

import asyncio
import logging
import os
from typing import Optional

from django.conf import settings
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from apps.common.exceptions import (LLMContentFilterError,
                                    LLMQuotaExceededError, LLMRateLimitError)

logger = logging.getLogger(__name__)


def _get_user_id(config: dict) -> int:
    """从 RunnableConfig 中提取 user_id"""
    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        raise ValueError("user_id not found in RunnableConfig")
    return int(user_id)


def get_common_tools() -> list[BaseTool]:
    """返回所有 SubAgent 共享的公共工具列表。

    包含 mem_search（只读记忆查询）和 web_search（网络搜索，
    受 BRAVE_SEARCH_API_KEY 条件控制）。
    """
    from apps.graph.tools.memory import mem_search

    tools: list[BaseTool] = [mem_search]

    if getattr(settings, "BRAVE_SEARCH_API_KEY", ""):
        from apps.graph.tools.search import web_search

        tools.append(web_search)

    return tools


def _merge_tools(specific_tools: list, common_tools: list) -> list:
    """合并专属工具和公共工具，按工具名去重。

    专属工具优先保留，公共工具中同名的跳过。
    """
    seen = {t.name for t in specific_tools}
    merged = list(specific_tools)
    for t in common_tools:
        if t.name not in seen:
            merged.append(t)
            seen.add(t.name)
    return merged


async def _get_llm_instance(llm: Optional[ChatOpenAI] = None) -> ChatOpenAI:
    """获取 LLM 实例。

    优先使用传入的 llm，否则尝试 Django 模式的 get_llm()，
    降级为环境变量配置。
    """
    if llm is not None:
        return llm

    try:
        from apps.graph.agent import get_llm

        return await get_llm()
    except Exception:
        logger.debug("Django get_llm() failed, falling back to env config")
        return ChatOpenAI(
            base_url=os.environ.get(
                "LLM_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"
            ),
            api_key=os.environ.get("LLM_API_KEY", "not-needed"),
            model=os.environ.get("LLM_MODEL_NAME", "deepseek-v3-1-terminus"),
            streaming=True,
            stream_usage=True,
        )


async def run_subagent(
    task: str,
    config: dict,
    tools: list,
    prompt: str,
    llm: Optional[ChatOpenAI] = None,
    name: str = "subagent",
    timeout: Optional[int] = None,
) -> str:
    """SubAgent 工厂函数。

    创建 react agent 执行任务，自动注入公共工具，
    带超时控制和异常处理。

    Args:
        task: 主 agent 提炼的任务描述
        config: RunnableConfig，包含 user_id
        tools: SubAgent 专属工具列表
        prompt: SubAgent 内部 system prompt
        llm: 可选的 LLM 实例，默认从 get_llm() 获取
        name: Agent 名称，用于 Langfuse trace 区分
        timeout: 可选的超时秒数，默认使用 SUBAGENT_TIMEOUT (60s)

    Returns:
        SubAgent 执行结果文本
    """
    user_id = _get_user_id(config)
    timeout = timeout or getattr(settings, "SUBAGENT_TIMEOUT", 60)

    model = await _get_llm_instance(llm)

    # 合并专属工具和公共工具（按工具名去重）
    common = get_common_tools()
    all_tools = _merge_tools(tools, common)

    agent = create_react_agent(model=model, tools=all_tools, prompt=prompt, name=name)

    try:
        # 转发父 config 的全部 configurable 键（支持 attachment_uuids/stop_event 等）
        configurable = dict(config.get("configurable", {}))
        configurable["user_id"] = user_id
        async with asyncio.timeout(timeout):
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={"configurable": configurable},
            )
        return result["messages"][-1].content
    except asyncio.TimeoutError:
        logger.warning("SubAgent timeout: user_id=%d, task=%s", user_id, task[:100])
        return f"该操作执行超时（{timeout}秒），请稍后重试"
    except LLMRateLimitError:
        return "请求过于频繁，请等待后重试"
    except LLMContentFilterError:
        return "消息内容可能包含敏感信息，请修改后重试"
    except LLMQuotaExceededError:
        return "服务配额已用尽，请联系管理员"
    except Exception as e:
        logger.exception("SubAgent error: user_id=%d, task=%s", user_id, task[:100])
        return "服务暂时不可用，请稍后重试"
