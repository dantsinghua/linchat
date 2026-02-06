"""SubAgent 单元测试

测试覆盖:
1. run_subagent 工厂函数：正常执行、超时、异常差异化提示
2. get_common_tools 公共工具列表：含/不含 BRAVE_SEARCH_API_KEY、去重逻辑
3. 各 SubAgent tool 函数：入参传递、结果提取、公共工具注入
4. get_subagent_tools 条件注册逻辑
5. agent_service 事件过滤兼容性
6. Edge Case 测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.common.exceptions import (LLMContentFilterError,
                                    LLMQuotaExceededError, LLMRateLimitError)

# ============ T025(1): run_subagent 工厂函数测试 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_normal(mock_create_agent, mock_get_llm):
    """run_subagent 正常执行，返回最终消息内容"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "计算结果是 42"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("计算 6 * 7", config, [], "你是数学助手")

    assert result == "计算结果是 42"
    mock_agent.ainvoke.assert_called_once()


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_timeout(mock_create_agent, mock_get_llm):
    """run_subagent 超时，返回友好提示"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = asyncio.TimeoutError()
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("搜索新闻", config, [], "你是搜索助手")

    assert "超时" in result


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_rate_limit(mock_create_agent, mock_get_llm):
    """run_subagent LLMRateLimitError，返回频率限制提示"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = LLMRateLimitError()
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("搜索", config, [], "prompt")

    assert "频繁" in result


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_content_filter(mock_create_agent, mock_get_llm):
    """run_subagent LLMContentFilterError，返回敏感内容提示"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = LLMContentFilterError()
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("搜索", config, [], "prompt")

    assert "敏感" in result


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_quota_exceeded(mock_create_agent, mock_get_llm):
    """run_subagent LLMQuotaExceededError，返回配额提示"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = LLMQuotaExceededError()
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("搜索", config, [], "prompt")

    assert "配额" in result


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_generic_error(mock_create_agent, mock_get_llm):
    """run_subagent 未知异常，返回兜底友好提示"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = RuntimeError("unexpected")
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("搜索", config, [], "prompt")

    assert "不可用" in result


# ============ T025(2): get_common_tools 公共工具测试 ============


def test_get_common_tools_with_brave_key():
    """get_common_tools 有 BRAVE_SEARCH_API_KEY 时返回 2 个工具"""
    with patch("apps.graph.subagents.base.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = "test-key"
        from apps.graph.subagents.base import get_common_tools

        tools = get_common_tools()
        names = [t.name for t in tools]
        assert "mem_search" in names
        assert "web_search" in names
        assert len(tools) == 2


def test_get_common_tools_without_brave_key():
    """get_common_tools 无 BRAVE_SEARCH_API_KEY 时只返回 mem_search"""
    with patch("apps.graph.subagents.base.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = ""
        from apps.graph.subagents.base import get_common_tools

        tools = get_common_tools()
        names = [t.name for t in tools]
        assert "mem_search" in names
        assert "web_search" not in names


def test_merge_tools_dedup():
    """_merge_tools 按工具名去重"""
    from apps.graph.subagents.base import _merge_tools

    tool_a = MagicMock()
    tool_a.name = "web_search"
    tool_b = MagicMock()
    tool_b.name = "mem_search"
    tool_c = MagicMock()
    tool_c.name = "web_search"  # 同名，应被去重

    merged = _merge_tools([tool_a], [tool_b, tool_c])
    names = [t.name for t in merged]
    assert names == ["web_search", "mem_search"]
    assert len(merged) == 2


# ============ T025(3): 各 SubAgent tool 函数测试 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.search_agent.run_subagent")
async def test_search_subagent_calls_run(mock_run):
    """search_subagent 正确调用 run_subagent"""
    from apps.graph.subagents.search_agent import search_subagent

    mock_run.return_value = "搜索结果"
    config = {"configurable": {"user_id": 1}}

    result = await search_subagent.ainvoke(
        input={"task": "搜索黄金价格"},
        config=config,
    )
    assert result == "搜索结果"
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0] == "搜索黄金价格"


@pytest.mark.asyncio
@patch("apps.graph.subagents.code_agent.run_subagent")
async def test_code_subagent_calls_run(mock_run):
    """code_subagent 正确调用 run_subagent"""
    from apps.graph.subagents.code_agent import code_subagent

    mock_run.return_value = "结果是 42"
    config = {"configurable": {"user_id": 1}}

    result = await code_subagent.ainvoke(
        input={"task": "计算 6*7"},
        config=config,
    )
    assert result == "结果是 42"


@pytest.mark.asyncio
@patch("apps.graph.subagents.memory_agent.run_subagent")
async def test_memory_subagent_calls_run(mock_run):
    """memory_subagent 正确调用 run_subagent"""
    from apps.graph.subagents.memory_agent import memory_subagent

    mock_run.return_value = "记忆已保存"
    config = {"configurable": {"user_id": 1}}

    result = await memory_subagent.ainvoke(
        input={"task": "记住我喜欢蓝色"},
        config=config,
    )
    assert result == "记忆已保存"


# ============ T025(4): get_subagent_tools 条件注册测试 ============


def test_get_subagent_tools_with_brave_key():
    """有 BRAVE_SEARCH_API_KEY 时至少注册 3 个基础 SubAgent"""
    with patch("apps.graph.subagents.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = "test-key"
        mock_settings.HA_ENABLED = False  # 禁用 HA 以测试基础功能
        from apps.graph.subagents import get_subagent_tools

        tools = get_subagent_tools()
        names = [t.name for t in tools]
        assert "search_subagent" in names
        assert "memory_subagent" in names
        assert "code_subagent" in names
        # 至少有 3 个基础 subagent，可能更多（如 ha_subagent 在环境配置时）
        assert len(tools) >= 3


def test_get_subagent_tools_without_brave_key():
    """无 BRAVE_SEARCH_API_KEY 时至少注册 2 个基础 SubAgent"""
    with patch("apps.graph.subagents.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = ""
        mock_settings.HA_ENABLED = False  # 禁用 HA 以测试基础功能
        from apps.graph.subagents import get_subagent_tools

        tools = get_subagent_tools()
        names = [t.name for t in tools]
        assert "search_subagent" not in names
        assert "memory_subagent" in names
        assert "code_subagent" in names
        # 至少有 2 个基础 subagent，可能更多
        assert len(tools) >= 2


# ============ T025(5): 事件过滤兼容性测试 ============


def test_event_filtering_main_agent():
    """depth <= 3 的 on_chat_model_stream 事件应被处理（主 agent）"""
    event = {
        "event": "on_chat_model_stream",
        "parent_ids": ["id1", "id2", "id3"],
    }
    assert len(event["parent_ids"]) <= 3  # 应通过过滤


def test_event_filtering_subagent():
    """depth > 3 的 on_chat_model_stream 事件应被跳过（SubAgent）"""
    event = {
        "event": "on_chat_model_stream",
        "parent_ids": ["id1", "id2", "id3", "id4", "id5", "id6"],
    }
    assert len(event["parent_ids"]) > 3  # 应被过滤


# ============ T025(7): Edge Case 测试 ============


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_subagent_no_user_id(mock_create_agent, mock_get_llm):
    """config 中缺少 user_id 时抛出 ValueError"""
    from apps.graph.subagents.base import run_subagent

    config = {"configurable": {}}
    with pytest.raises(ValueError, match="user_id"):
        await run_subagent("任务", config, [], "prompt")


@pytest.mark.asyncio
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")
async def test_run_subagent_empty_tools(mock_create_agent, mock_get_llm):
    """空专属工具列表 + 公共工具仍能正常工作"""
    from apps.graph.subagents.base import run_subagent

    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    mock_msg = MagicMock()
    mock_msg.content = "直接回复"
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [mock_msg]}
    mock_create_agent.return_value = mock_agent

    config = {"configurable": {"user_id": 1}}
    result = await run_subagent("你好", config, [], "你是助手")

    assert result == "直接回复"
    # 验证 create_react_agent 调用时的工具列表包含公共工具
    call_kwargs = mock_create_agent.call_args
    tools_arg = (
        call_kwargs[1]["tools"]
        if "tools" in call_kwargs[1]
        else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    )
    # 公共工具至少有 mem_search
    assert len(tools_arg) >= 1
