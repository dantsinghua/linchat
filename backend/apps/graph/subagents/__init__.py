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

    # Home Assistant SubAgent：需要 HA_ENABLED
    if getattr(settings, "HA_ENABLED", False):
        from .ha_agent import ha_subagent

        tools.append(ha_subagent)

    # 多模态 SubAgent：始终启用（模型配置由 tool 内部检查）
    from .multimodal_agent import multimodal_subagent

    tools.append(multimodal_subagent)

    # 文档 SubAgent：始终启用 (011-document-subagent-rag)
    from .document_agent import document_subagent

    tools.append(document_subagent)

    # 历史搜索工具：始终启用
    from apps.graph.tools.history import history_search

    tools.append(history_search)

    # 公众号知识库检索工具：OA_SEARCH_ENABLED 开关灰度
    if getattr(settings, "OA_SEARCH_ENABLED", False):
        from apps.graph.tools.oa_search import oa_search

        tools.append(oa_search)

    return tools
