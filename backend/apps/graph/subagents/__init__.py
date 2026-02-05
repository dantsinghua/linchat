"""SubAgent 模块 — 封装各类 SubAgent 定义和注册逻辑。

通过 get_subagent_tools() 获取当前可用的 SubAgent 工具列表，
主 agent 使用这些工具替代直接绑定的搜索/记忆/代码执行工具。
"""

from django.conf import settings
from langchain_core.tools import BaseTool


def get_subagent_tools() -> list[BaseTool]:
    """根据配置条件组装可用的 SubAgent 工具列表。

    Returns:
        可注册到主 agent 的 SubAgent tool 函数列表
    """
    tools: list[BaseTool] = []

    # 搜索 SubAgent：需要 BRAVE_SEARCH_API_KEY
    if getattr(settings, "BRAVE_SEARCH_API_KEY", ""):
        from .search_agent import search_subagent

        tools.append(search_subagent)

    # 记忆 SubAgent：始终启用
    from .memory_agent import memory_subagent

    tools.append(memory_subagent)

    # 代码执行 SubAgent：始终启用
    from .code_agent import code_subagent

    tools.append(code_subagent)

    return tools
