"""
LangGraph 上下文工具集

context_compact / context_extract / context_prune

双模式支持：Django 环境调用 LLM，独立模式（langgraph dev）直接截断返回。

参考: behavior-model.md §2, spec.md FR-004
"""

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _is_django_mode() -> bool:
    """检测是否在 Django 环境中运行"""
    try:
        import django

        return django.apps.apps.ready
    except Exception:
        return False


@tool
async def context_compact(content: str) -> str:
    """压缩对话内容为简洁摘要，保留关键信息。

    Args:
        content: 待压缩的对话内容文本

    Returns:
        压缩后的摘要文本
    """
    if not _is_django_mode():
        return content[:500] + "..." if len(content) > 500 else content

    from apps.graph.agent import get_llm
    from apps.context import COMPACTION_PROMPT_TEMPLATE

    llm = await get_llm()
    from apps.graph.tools import cap_tool_result
    prompt = COMPACTION_PROMPT_TEMPLATE.format(conversation_text=content)
    response = await llm.ainvoke(prompt)
    return cap_tool_result(str(response.content), "context_compact")


@tool
async def context_extract(content: str, query: str) -> str:
    """从内容中提取与查询相关的片段。

    Args:
        content: 待提取的原始内容
        query: 用户查询，用于确定提取方向

    Returns:
        提取出的相关片段
    """
    if not _is_django_mode():
        return f"[独立模式] 提取与「{query}」相关的内容: {content[:300]}..."

    from apps.graph.agent import get_llm

    llm = await get_llm()
    prompt = (
        f"从以下内容中提取与问题「{query}」最相关的信息片段。"
        f"只返回相关内容，不要添加解释。\n\n{content}"
    )
    from apps.graph.tools import cap_tool_result
    response = await llm.ainvoke(prompt)
    return cap_tool_result(str(response.content), "context_extract")


@tool
async def context_prune(content: str) -> str:
    """删除内容中的冗余部分（问候语、重复确认等），保留核心信息。

    Args:
        content: 待剪枝的内容文本

    Returns:
        剪枝后的精简文本
    """
    if not _is_django_mode():
        return content[:500] + "..." if len(content) > 500 else content

    from apps.graph.agent import get_llm

    llm = await get_llm()
    prompt = (
        "请删除以下内容中的冗余部分（问候语、重复确认、过渡性对话），"
        "只保留核心信息和关键结论。直接输出结果，不要解释。\n\n"
        f"{content}"
    )
    from apps.graph.tools import cap_tool_result
    response = await llm.ainvoke(prompt)
    return cap_tool_result(str(response.content), "context_prune")


# 工具集导出
CONTEXT_TOOLS = [context_compact, context_extract, context_prune]
