"""LangGraph 工具集"""

import logging

from django.conf import settings

from apps.graph.tools.context import CONTEXT_TOOLS
from apps.graph.tools.memory import MEMORY_TOOLS
from apps.graph.tools.python_repl import REPL_TOOLS
from apps.graph.tools.search import SEARCH_TOOLS

logger = logging.getLogger(__name__)

__all__ = ["CONTEXT_TOOLS", "MEMORY_TOOLS", "SEARCH_TOOLS", "REPL_TOOLS", "cap_tool_result"]


def cap_tool_result(text: str, tool_name: str) -> str:
    """工具结果 token 截断保护

    超过 MAX_TOOL_RESULT_TOKENS (1500) 时截断并附加标记。
    超过 2000 tokens 时记录 WARNING 日志。
    """
    from apps.common.tokenizer import count_tokens

    max_tokens = getattr(settings, "MAX_TOOL_RESULT_TOKENS", 1500)
    token_count = count_tokens(text)

    if token_count > 2000:
        logger.warning(
            "Large tool result: tool=%s, tokens=%d", tool_name, token_count,
        )

    if token_count <= max_tokens:
        return text

    # 二分查找截断点
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1

    return text[:low] + "\n[结果已截断]"
