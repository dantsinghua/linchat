"""记忆 SubAgent — 封装 mem_* 工具的记忆管理助手

通过 run_subagent() 创建内部 react agent，
自动注入公共工具（web_search，mem_search 已在 MEMORY_TOOLS 中，去重跳过）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.context.loader import render
from apps.graph.subagents.base import run_subagent
from apps.graph.tools.memory import MEMORY_TOOLS


@tool
async def memory_subagent(task: str, config: RunnableConfig) -> str:
    """管理用户的长期记忆。当用户要求记住、回忆、更新或删除个人信息时使用。"""
    return await run_subagent(
        task, config, list(MEMORY_TOOLS), render("memory_subagent.j2"), name="memory_subagent"
    )
