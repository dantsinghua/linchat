"""
记忆工具集测试 [T056]

mem_search / mem_cache / mem_update / mem_delete 委托 MemoryService 验证。
user_id 通过 RunnableConfig 隐式注入 [R-004]。
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

# 辅助函数：构造包含 user_id 的 RunnableConfig
_CONFIG = lambda uid: {"configurable": {"thread_id": f"user_{uid}", "user_id": uid}}

# MemoryService 在工具内部延迟导入，需 patch 源模块
_SVC = "apps.memory.services.MemoryService"


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

    def test_tool_schemas_hide_user_id(self) -> None:
        """工具 schema 中不包含 user_id（LLM 不可见）"""
        for t in MEMORY_TOOLS:
            schema = t.get_input_schema().model_json_schema()
            props = schema.get("properties", {})
            assert "user_id" not in props, f"{t.name} schema exposes user_id"
            assert "config" not in props, f"{t.name} schema exposes config"

    @patch(f"{_SVC}.search_memory", new_callable=AsyncMock)
    def test_mem_search_results(self, mock_search) -> None:
        """mem_search 返回格式化搜索结果（含 memory_id）"""
        mock_memory = MagicMock()
        mock_memory.id = 5
        mock_memory.content = "Python 学习笔记"
        mock_search.return_value = [
            {"memory": mock_memory, "score": 0.85, "match_type": "hybrid"},
        ]

        result = run_async(
            mem_search.ainvoke({"query": "Python"}, config=_CONFIG(1))
        )

        assert "Python 学习笔记" in result
        assert "id=5" in result
        mock_search.assert_called_once_with(
            user_id=1, query="Python", limit=5,
        )

    @patch(f"{_SVC}.search_memory", new_callable=AsyncMock)
    def test_mem_search_empty(self, mock_search) -> None:
        """mem_search 无结果"""
        mock_search.return_value = []

        result = run_async(
            mem_search.ainvoke({"query": "不存在的内容"}, config=_CONFIG(1))
        )

        assert "未找到" in result

    @patch(f"{_SVC}.create_memory", new_callable=AsyncMock)
    def test_mem_cache(self, mock_create) -> None:
        """mem_cache 创建记忆"""
        mock_memory = MagicMock()
        mock_memory.id = 42
        mock_create.return_value = mock_memory

        result = run_async(
            mem_cache.ainvoke({"content": "新记忆"}, config=_CONFIG(1))
        )

        assert "42" in result
        assert "已保存" in result
        mock_create.assert_called_once_with(
            user_id=1, content="新记忆", name=None,
        )

    @patch(f"{_SVC}.update_memory", new_callable=AsyncMock)
    def test_mem_update_success(self, mock_update) -> None:
        """mem_update 更新记忆"""
        result = run_async(
            mem_update.ainvoke(
                {"memory_id": 10, "content": "更新内容"},
                config=_CONFIG(1),
            )
        )

        assert "已更新" in result
        mock_update.assert_called_once_with(
            memory_id=10, user_id=1, content="更新内容",
        )

    @patch(f"{_SVC}.update_memory", new_callable=AsyncMock)
    def test_mem_update_not_found(self, mock_update) -> None:
        """mem_update 记忆不存在"""
        mock_update.side_effect = Exception("记忆不存在")

        result = run_async(
            mem_update.ainvoke(
                {"memory_id": 999, "content": "test"},
                config=_CONFIG(1),
            )
        )

        assert "失败" in result

    @patch(f"{_SVC}.delete_memory", new_callable=AsyncMock)
    def test_mem_delete_success(self, mock_delete) -> None:
        """mem_delete 删除记忆"""
        mock_delete.return_value = True

        result = run_async(
            mem_delete.ainvoke({"memory_id": 10}, config=_CONFIG(1))
        )

        assert "已删除" in result

    @patch(f"{_SVC}.delete_memory", new_callable=AsyncMock)
    def test_mem_delete_not_found(self, mock_delete) -> None:
        """mem_delete 记忆不存在"""
        mock_delete.side_effect = Exception("记忆不存在")

        result = run_async(
            mem_delete.ainvoke({"memory_id": 999}, config=_CONFIG(1))
        )

        assert "失败" in result

    @patch(f"{_SVC}.search_memory", new_callable=AsyncMock)
    def test_user_id_injected_from_config(self, mock_search) -> None:
        """user_id 从 RunnableConfig 正确注入"""
        mock_search.return_value = []

        run_async(
            mem_search.ainvoke(
                {"query": "test", "limit": 3},
                config=_CONFIG(42),
            )
        )

        mock_search.assert_called_once_with(
            user_id=42, query="test", limit=3,
        )

    def test_missing_user_id_raises(self) -> None:
        """缺少 user_id 时抛出 ValueError"""
        with pytest.raises(Exception):
            run_async(
                mem_search.ainvoke(
                    {"query": "test"},
                    config={"configurable": {"thread_id": "user_1"}},
                )
            )
