"""SubAgent 自主性行为测试 [T026]

验证 SubAgent 内部工具列表包含公共工具，
确保 SubAgent 有能力自主使用 mem_search/web_search。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============ T026(1): SubAgent 内部工具列表验证 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_code_subagent_has_common_tools(mock_create_agent, mock_get_llm):
    """code_subagent 内部 react agent 的工具列表包含公共工具"""
    from apps.graph.subagents.base import run_subagent
    from apps.graph.tools.python_repl import REPL_TOOLS

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "结果"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    await run_subagent("计算", config, list(REPL_TOOLS), "prompt")

    # 验证 create_react_agent 收到的工具列表
    call_kwargs = mock_create_agent.call_args
    tools_arg = (
        call_kwargs[1]["tools"]
        if "tools" in call_kwargs[1]
        else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    )
    tool_names = [t.name for t in tools_arg]

    # 包含专属工具 python_exec
    assert "python_exec" in tool_names
    # 包含公共工具 mem_search
    assert "mem_search" in tool_names


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
@patch("apps.graph.subagents.base.settings")
async def test_search_subagent_has_mem_search(
    mock_settings, mock_create_agent, mock_get_llm
):
    """search_subagent 内部 react agent 的工具列表包含 mem_search"""
    mock_settings.BRAVE_SEARCH_API_KEY = "test-key"
    mock_settings.SUBAGENT_TIMEOUT = 60

    from apps.graph.subagents.base import run_subagent
    from apps.graph.tools.search import SEARCH_TOOLS

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "搜索结果"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    await run_subagent("搜索", config, list(SEARCH_TOOLS), "prompt")

    call_kwargs = mock_create_agent.call_args
    tools_arg = (
        call_kwargs[1]["tools"]
        if "tools" in call_kwargs[1]
        else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    )
    tool_names = [t.name for t in tools_arg]

    assert "web_search" in tool_names
    assert "mem_search" in tool_names


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
@patch("apps.graph.subagents.base.settings")
async def test_memory_subagent_has_web_search(
    mock_settings, mock_create_agent, mock_get_llm
):
    """memory_subagent 内部 react agent 的工具列表包含 web_search（用于验证信息准确性）"""
    mock_settings.BRAVE_SEARCH_API_KEY = "test-key"
    mock_settings.SUBAGENT_TIMEOUT = 60

    from apps.graph.subagents.base import run_subagent
    from apps.graph.tools.memory import MEMORY_TOOLS

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "已保存"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    await run_subagent("记住", config, list(MEMORY_TOOLS), "prompt")

    call_kwargs = mock_create_agent.call_args
    tools_arg = (
        call_kwargs[1]["tools"]
        if "tools" in call_kwargs[1]
        else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    )
    tool_names = [t.name for t in tools_arg]

    assert "mem_search" in tool_names
    assert "mem_cache" in tool_names
    assert "web_search" in tool_names


# ============ T026(4): SubAgent 不回传主 agent 测试 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_subagent_returns_complete_result(mock_create_agent, mock_get_llm):
    """SubAgent 返回完整结果，不回传不完整信息给主 agent"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    # 模拟 SubAgent 返回完整结果
    mock_msg = MagicMock()
    mock_msg.content = "计算完成：6 × 7 = 42"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("计算 6*7", config, [], "prompt")

    assert result == "计算完成：6 × 7 = 42"
    assert "不完整" not in result
    assert "信息不足" not in result


# ============ T026(5): 主 agent 与 SubAgent 交互边界 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_subagent_called_once_returns_final(mock_create_agent, mock_get_llm):
    """run_subagent 只调用一次 ainvoke，SubAgent 内部完成所有工具调用"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "最终结果"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    await run_subagent("任务", config, [], "prompt")

    # ainvoke 只被调用一次（SubAgent react agent 内部循环由 langgraph 管理）
    mock_agent.ainvoke.assert_called_once()
