"""Home Assistant SubAgent — 智能家居控制助手

通过 run_subagent() 创建内部 react agent，
管理 ha_query / ha_control / ha_diagnose 三个专属工具，
自动注入公共工具（mem_search + web_search）。

参考: specs/007-home-assistant-tools/, M2b-home-assistant-requirements.md
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.context.loader import render
from apps.graph.subagents.base import run_subagent
from apps.graph.tools.homeassistant import HA_TOOLS


@tool
async def ha_subagent(task: str, config: RunnableConfig) -> str:
    """控制和查询智能家居设备。当用户需要控制灯光、空调、窗帘、
    开关等设备，查询设备状态，或诊断智能家居问题时使用。"""
    return await run_subagent(
        task, config, list(HA_TOOLS), render("ha_subagent.j2"), name="ha_subagent"
    )
