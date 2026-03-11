"""代码执行 SubAgent — 封装 python_exec 工具的代码执行助手

通过 run_subagent() 创建内部 react agent，
自动注入公共工具（mem_search + web_search）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.context.loader import render
from apps.graph.subagents.base import run_subagent
from apps.graph.tools.python_repl import REPL_TOOLS


@tool
async def code_subagent(task: str, config: RunnableConfig) -> str:
    """执行 Python 代码进行计算、数据处理或验证。当用户需要数学计算、统计分析、数据转换或明确要求运行代码时使用。"""
    return await run_subagent(
        task, config, list(REPL_TOOLS), render("code_subagent.j2"), name="code_subagent"
    )
