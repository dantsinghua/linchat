"""
上下文工具集测试 [T055]

context_compact / context_extract / context_prune 输入输出验证。
mock LLM 调用。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.graph.tools.context import (
    CONTEXT_TOOLS,
    context_compact,
    context_extract,
    context_prune,
)
from tests.helpers import run_async


class TestContextTools:
    """上下文工具集测试"""

    def test_tools_registered(self) -> None:
        """三个工具已注册"""
        assert len(CONTEXT_TOOLS) == 3
        tool_names = {t.name for t in CONTEXT_TOOLS}
        assert "context_compact" in tool_names
        assert "context_extract" in tool_names
        assert "context_prune" in tool_names

    @patch("apps.graph.agent.get_llm")
    def test_context_compact(self, mock_get_llm) -> None:
        """context_compact 调用 LLM 压缩内容"""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "压缩后的摘要"
        mock_llm.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        result = run_async(
            context_compact.ainvoke({"content": "很长的对话内容..."})
        )

        assert "压缩后的摘要" in result
        mock_llm.ainvoke.assert_called_once()

    @patch("apps.graph.agent.get_llm")
    def test_context_extract(self, mock_get_llm) -> None:
        """context_extract 调用 LLM 提取相关片段"""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "提取的相关内容"
        mock_llm.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        result = run_async(
            context_extract.ainvoke({
                "content": "原始内容...",
                "query": "关于 Python",
            })
        )

        assert "提取的相关内容" in result

    @patch("apps.graph.agent.get_llm")
    def test_context_prune(self, mock_get_llm) -> None:
        """context_prune 调用 LLM 剪枝冗余"""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "精简后的内容"
        mock_llm.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        result = run_async(
            context_prune.ainvoke({"content": "你好！好的，我来帮你。核心结论是..."})
        )

        assert "精简后的内容" in result
