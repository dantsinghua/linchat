"""
记忆工具集测试 [T056]

mem_search / mem_cache / mem_update / mem_delete 委托 MemoryService 验证。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.graph.tools.memory import (
    MEMORY_TOOLS,
    mem_cache,
    mem_delete,
    mem_search,
    mem_update,
)
from tests.helpers import run_async


class TestMemoryTools:
    """记忆工具集测试"""

    def test_tools_registered(self) -> None:
        """四个工具已注册"""
        assert len(MEMORY_TOOLS) == 4
        tool_names = {t.name for t in MEMORY_TOOLS}
        assert "mem_search" in tool_names
        assert "mem_cache" in tool_names
        assert "mem_update" in tool_names
        assert "mem_delete" in tool_names

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_search_results(self, mock_svc) -> None:
        """mem_search 返回格式化搜索结果"""
        mock_memory = MagicMock()
        mock_memory.content = "Python 学习笔记"
        mock_svc.search_memory = AsyncMock(return_value=[
            {"memory": mock_memory, "score": 0.85, "match_type": "hybrid"},
        ])

        result = run_async(
            mem_search.ainvoke({"user_id": 1, "query": "Python"})
        )

        assert "Python 学习笔记" in result
        assert "0.85" in result
        mock_svc.search_memory.assert_called_once_with(
            user_id=1, query="Python", limit=5,
        )

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_search_empty(self, mock_svc) -> None:
        """mem_search 无结果"""
        mock_svc.search_memory = AsyncMock(return_value=[])

        result = run_async(
            mem_search.ainvoke({"user_id": 1, "query": "不存在的内容"})
        )

        assert "未找到" in result

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_cache(self, mock_svc) -> None:
        """mem_cache 创建记忆"""
        mock_memory = MagicMock()
        mock_memory.id = 42
        mock_svc.create_memory = AsyncMock(return_value=mock_memory)

        result = run_async(
            mem_cache.ainvoke({"user_id": 1, "content": "新记忆"})
        )

        assert "42" in result
        assert "已保存" in result
        mock_svc.create_memory.assert_called_once_with(
            user_id=1, content="新记忆", name=None,
        )

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_update_success(self, mock_svc) -> None:
        """mem_update 更新记忆"""
        mock_svc.update_memory = AsyncMock()

        result = run_async(
            mem_update.ainvoke({
                "user_id": 1, "memory_id": 10, "content": "更新内容",
            })
        )

        assert "已更新" in result
        mock_svc.update_memory.assert_called_once_with(
            memory_id=10, user_id=1, content="更新内容",
        )

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_update_not_found(self, mock_svc) -> None:
        """mem_update 记忆不存在"""
        mock_svc.update_memory = AsyncMock(side_effect=Exception("记忆不存在"))

        result = run_async(
            mem_update.ainvoke({
                "user_id": 1, "memory_id": 999, "content": "test",
            })
        )

        assert "失败" in result

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_delete_success(self, mock_svc) -> None:
        """mem_delete 删除记忆"""
        mock_svc.delete_memory = AsyncMock(return_value=True)

        result = run_async(
            mem_delete.ainvoke({"user_id": 1, "memory_id": 10})
        )

        assert "已删除" in result

    @patch("apps.graph.tools.memory.MemoryService")
    def test_mem_delete_not_found(self, mock_svc) -> None:
        """mem_delete 记忆不存在"""
        mock_svc.delete_memory = AsyncMock(side_effect=Exception("记忆不存在"))

        result = run_async(
            mem_delete.ainvoke({"user_id": 1, "memory_id": 999})
        )

        assert "失败" in result

    @patch("apps.graph.tools.memory.MemoryService")
    def test_user_id_passed_correctly(self, mock_svc) -> None:
        """user_id 正确传递"""
        mock_svc.search_memory = AsyncMock(return_value=[])

        run_async(
            mem_search.ainvoke({"user_id": 42, "query": "test", "limit": 3})
        )

        mock_svc.search_memory.assert_called_once_with(
            user_id=42, query="test", limit=3,
        )
