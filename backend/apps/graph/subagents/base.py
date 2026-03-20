import asyncio
import logging
import os
from typing import Optional

from django.conf import settings
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from apps.common.exceptions import (LLMContentFilterError,
                                    LLMQuotaExceededError, LLMRateLimitError)
from apps.graph.tools.user_id import get_user_id as _get_user_id

logger = logging.getLogger(__name__)


def get_common_tools() -> list[BaseTool]:
    from apps.graph.tools.memory import mem_search
    tools: list[BaseTool] = [mem_search]
    if getattr(settings, "BRAVE_SEARCH_API_KEY", ""):
        from apps.graph.tools.search import web_search
        tools.append(web_search)
    return tools


def _merge_tools(specific_tools: list, common_tools: list) -> list:
    seen = {t.name for t in specific_tools}
    return list(specific_tools) + [t for t in common_tools if t.name not in seen]


async def _get_llm_instance(llm: Optional[ChatOpenAI] = None) -> ChatOpenAI:
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
    recursion_limit: Optional[int] = None,
) -> str:
    user_id = _get_user_id(config)
    timeout = timeout or getattr(settings, "SUBAGENT_TIMEOUT", 60)
    model = await _get_llm_instance(llm)
    all_tools = _merge_tools(tools, get_common_tools())
    agent = create_react_agent(model=model, tools=all_tools, prompt=prompt, name=name)
    try:
        configurable = {**config.get("configurable", {}), "user_id": user_id}
        run_config: dict = {"configurable": configurable}
        if recursion_limit:
            run_config["recursion_limit"] = recursion_limit
        async with asyncio.timeout(timeout):
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config=run_config,
            )
        return result["messages"][-1].content
    except asyncio.TimeoutError:
        logger.warning("SubAgent timeout: user_id=%d, task=%s", user_id, task[:100])
        return f"该操作执行超时（{timeout}秒），请稍后重试"
    except GraphRecursionError:
        logger.warning(
            "SubAgent recursion limit: user_id=%d, name=%s, task=%s",
            user_id, name, task[:100],
        )
        return (
            "⚠️ 工具调用次数超过限制，无法完成任务。"
            "建议：缩小查询范围，或针对文档的具体章节提问。"
        )
    except LLMRateLimitError:
        return "请求过于频繁，请等待后重试"
    except LLMContentFilterError:
        return "消息内容可能包含敏感信息，请修改后重试"
    except LLMQuotaExceededError:
        return "服务配额已用尽，请联系管理员"
    except Exception:
        logger.exception("SubAgent error: user_id=%d, task=%s", user_id, task[:100])
        return "服务暂时不可用，请稍后重试"
