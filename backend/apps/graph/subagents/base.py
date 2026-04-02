import asyncio
import logging
import os
import time
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
    t0 = time.monotonic()
    logger.info("[SubAgent] START: name=%s, user_id=%d, timeout=%ds, task='%s'", name, user_id, timeout, task[:100])

    t_llm = time.monotonic()
    model = await _get_llm_instance(llm)
    logger.info("[SubAgent] LLM ready: name=%s, model=%s, cost=%.0fms", name, getattr(model, "model_name", "unknown"), (time.monotonic() - t_llm) * 1000)

    all_tools = _merge_tools(tools, get_common_tools())
    tool_names = [t.name for t in all_tools]
    logger.info("[SubAgent] tools: name=%s, tools=%s", name, tool_names)

    agent = create_react_agent(model=model, tools=all_tools, prompt=prompt, name=name)
    try:
        configurable = {**config.get("configurable", {}), "user_id": user_id}
        run_config: dict = {"configurable": configurable}
        if recursion_limit:
            run_config["recursion_limit"] = recursion_limit
        t_invoke = time.monotonic()
        logger.info("[SubAgent] ainvoke START: name=%s", name)
        async with asyncio.timeout(timeout):
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config=run_config,
            )
        invoke_ms = (time.monotonic() - t_invoke) * 1000
        total_ms = (time.monotonic() - t0) * 1000
        msg_count = len(result.get("messages", []))
        # Log tool calls from message history
        tool_calls = [m for m in result.get("messages", []) if getattr(m, "type", "") == "tool"]
        tool_names_used = [m.name for m in tool_calls] if tool_calls else []
        content_preview = result["messages"][-1].content[:200] if result.get("messages") else "(empty)"
        logger.info("[SubAgent] END OK: name=%s, user_id=%d, invoke=%.0fms, total=%.0fms, msgs=%d, tools_used=%s, result='%s'", name, user_id, invoke_ms, total_ms, msg_count, tool_names_used, content_preview)
        return result["messages"][-1].content
    except asyncio.TimeoutError:
        total_ms = (time.monotonic() - t0) * 1000
        logger.warning("[SubAgent] TIMEOUT: name=%s, user_id=%d, timeout=%ds, elapsed=%.0fms, task='%s'", name, user_id, timeout, total_ms, task[:100])
        return f"该操作执行超时（{timeout}秒），请稍后重试"
    except GraphRecursionError:
        total_ms = (time.monotonic() - t0) * 1000
        logger.warning(
            "[SubAgent] RECURSION LIMIT: name=%s, user_id=%d, elapsed=%.0fms, task='%s'",
            name, user_id, total_ms, task[:100],
        )
        return (
            "⚠️ 工具调用次数超过限制，无法完成任务。"
            "建议：缩小查询范围，或针对文档的具体章节提问。"
        )
    except LLMRateLimitError:
        logger.warning("[SubAgent] LLM RATE LIMIT: name=%s, user_id=%d, elapsed=%.0fms", name, user_id, (time.monotonic() - t0) * 1000)
        return "请求过于频繁，请等待后重试"
    except LLMContentFilterError:
        return "消息内容可能包含敏感信息，请修改后重试"
    except LLMQuotaExceededError:
        return "服务配额已用尽，请联系管理员"
    except Exception:
        total_ms = (time.monotonic() - t0) * 1000
        logger.exception("[SubAgent] ERROR: name=%s, user_id=%d, elapsed=%.0fms, task='%s'", name, user_id, total_ms, task[:100])
        return "服务暂时不可用，请稍后重试"
