"""搜索 SubAgent — 封装 web_search 工具的搜索助手

通过 run_subagent() 创建内部 react agent，自动注入公共工具（mem_search）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.context.loader import render
from apps.graph.subagents.base import run_subagent
from apps.graph.tools.search import SEARCH_TOOLS


@tool
async def search_subagent(task: str, config: RunnableConfig) -> str:
    """搜索互联网获取最新信息。当用户需要实时资讯（新闻、天气、股价、技术动态等）或需要查找特定网址、文档时使用。"""
    return await run_subagent(
        task, config, list(SEARCH_TOOLS), render("search_subagent.j2"), name="search_subagent"
    )
