"""
LangGraph 流程工厂集成测试 [T057]

验证四个工厂函数创建的 Agent 各自工具集正确、不越界：
- chat: 记忆工具集 (4) + extra_tools
- context: 上下文工具集 (3)
- memory: 记忆工具集 (4)
- cronMem: 无工具 (0)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tests.helpers import run_async


def _mock_checkpointer():
    """构造 mock checkpointer 上下文管理器"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        yield MagicMock()

    return _cm


class TestGetLlmConfig:
    """get_llm() 配置测试"""

    @patch("apps.graph.agent.model_service")
    def test_qwen3_enable_thinking_disabled(self, mock_svc) -> None:
        """Qwen3 模型传递 enable_thinking=False"""
        mock_svc.get_active_model.return_value = {
            "url": "http://localhost:8100/v1",
            "api_key": "test-key",
            "name": "qwen3-8b",
            "temperature": None,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
        }
        with patch("apps.graph.agent.ChatOpenAI") as mock_cls:
            from apps.graph.agent import get_llm

            run_async(get_llm())
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["extra_body"] == {"enable_thinking": False}

    @patch("apps.graph.agent.model_service")
    def test_non_qwen3_no_enable_thinking(self, mock_svc) -> None:
        """非 Qwen3 模型不传递 enable_thinking"""
        mock_svc.get_active_model.return_value = {
            "url": "http://localhost:8100/v1",
            "api_key": "test-key",
            "name": "deepseek-v3",
            "temperature": None,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
        }
        with patch("apps.graph.agent.ChatOpenAI") as mock_cls:
            from apps.graph.agent import get_llm

            run_async(get_llm())
            call_kwargs = mock_cls.call_args.kwargs
            assert "extra_body" not in call_kwargs

    @patch("apps.graph.agent.model_service")
    def test_qwen3_case_insensitive(self, mock_svc) -> None:
        """Qwen3 模型名称匹配不区分大小写"""
        mock_svc.get_active_model.return_value = {
            "url": "http://localhost:8100/v1",
            "api_key": "test-key",
            "name": "Qwen3-235B-A22B",
            "temperature": None,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
        }
        with patch("apps.graph.agent.ChatOpenAI") as mock_cls:
            from apps.graph.agent import get_llm

            run_async(get_llm())
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["extra_body"] == {"enable_thinking": False}


class TestAgentFactories:
    """四流程工厂测试"""

    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_chat_agent_has_memory_tools(
        self, mock_create, mock_llm
    ) -> None:
        """chat 流程包含记忆工具集"""
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from apps.graph.agent import create_chat_agent

        async def _run():
            async with create_chat_agent() as agent:
                return agent

        run_async(_run())

        # 验证 create_react_agent 被调用
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])
        tool_names = {t.name for t in tools}

        # 包含 4 个记忆工具
        assert "mem_search" in tool_names
        assert "mem_cache" in tool_names
        assert "mem_update" in tool_names
        assert "mem_delete" in tool_names

        # 不包含上下文工具
        assert "context_compact" not in tool_names
        assert "context_extract" not in tool_names
        assert "context_prune" not in tool_names

    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_chat_agent_no_checkpointer(
        self, mock_create, mock_llm
    ) -> None:
        """chat 流程不使用 checkpointer（避免历史 tool 消息累积）"""
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from apps.graph.agent import create_chat_agent

        async def _run():
            async with create_chat_agent() as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        # 验证 checkpointer 不在参数中
        assert "checkpointer" not in call_kwargs.kwargs

    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_chat_agent_with_extra_tools(
        self, mock_create, mock_llm
    ) -> None:
        """chat 流程支持 extra_tools 扩展"""
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from langchain_core.tools import tool

        @tool
        def my_custom_tool(x: str) -> str:
            """自定义工具"""
            return x

        from apps.graph.agent import create_chat_agent

        async def _run():
            async with create_chat_agent(extra_tools=[my_custom_tool]) as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])
        tool_names = {t.name for t in tools}

        assert "my_custom_tool" in tool_names
        assert "mem_search" in tool_names  # 记忆工具仍存在

    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_chat_agent_with_prompt(
        self, mock_create, mock_llm
    ) -> None:
        """chat 流程接受 prompt 参数"""
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        preamble = [MagicMock()]  # mock SystemMessage

        from apps.graph.agent import create_chat_agent

        async def _run():
            async with create_chat_agent(prompt=preamble) as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        prompt_arg = call_kwargs.kwargs.get("prompt")
        # _wrap_prompt 将 list 包装为 callable，验证 callable 被传入
        assert callable(prompt_arg)

    @patch("apps.graph.agent.get_checkpointer")
    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_context_agent_has_only_context_tools(
        self, mock_create, mock_llm, mock_cp
    ) -> None:
        """context 流程仅包含上下文工具集"""
        mock_cp.side_effect = _mock_checkpointer()
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from apps.graph.agent import create_context_agent

        async def _run():
            async with create_context_agent() as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])
        tool_names = {t.name for t in tools}

        # 仅包含 3 个上下文工具
        assert len(tools) == 3
        assert "context_compact" in tool_names
        assert "context_extract" in tool_names
        assert "context_prune" in tool_names

        # 不包含记忆工具
        assert "mem_search" not in tool_names

    @patch("apps.graph.agent.get_checkpointer")
    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_memory_agent_has_only_memory_tools(
        self, mock_create, mock_llm, mock_cp
    ) -> None:
        """memory 流程仅包含记忆工具集"""
        mock_cp.side_effect = _mock_checkpointer()
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from apps.graph.agent import create_memory_agent

        async def _run():
            async with create_memory_agent() as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])
        tool_names = {t.name for t in tools}

        # 仅包含 4 个记忆工具
        assert len(tools) == 4
        assert "mem_search" in tool_names
        assert "mem_cache" in tool_names
        assert "mem_update" in tool_names
        assert "mem_delete" in tool_names

        # 不包含上下文工具
        assert "context_compact" not in tool_names

    @patch("apps.graph.agent.get_checkpointer")
    @patch("apps.graph.agent.get_llm")
    @patch("apps.graph.agent.create_react_agent")
    def test_cronmem_agent_has_no_tools(
        self, mock_create, mock_llm, mock_cp
    ) -> None:
        """cronMem 流程无工具"""
        mock_cp.side_effect = _mock_checkpointer()
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        from apps.graph.agent import create_cronmem_agent

        async def _run():
            async with create_cronmem_agent() as agent:
                return agent

        run_async(_run())

        call_kwargs = mock_create.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])

        assert len(tools) == 0


class TestWrapPromptTrimming:
    """_wrap_prompt 历史消息裁剪测试"""

    def test_trims_long_history(self) -> None:
        """验证 _wrap_prompt 在历史超出预算时正确裁剪"""
        from apps.graph.agent import _wrap_prompt

        preamble = [SystemMessage(content="You are a helpful assistant.")]

        # 构造超长 messages 列表：每条约 100 token
        long_text = "这是一段较长的测试消息内容，用于填充 token 预算。" * 20
        messages = []
        for i in range(50):
            messages.append(HumanMessage(content=f"用户消息 {i}: {long_text}"))
            messages.append(AIMessage(content=f"助手回复 {i}: {long_text}"))

        # effective_window=5000, preamble_tokens=100 → history_budget ≈ 804
        wrapped = _wrap_prompt(preamble, preamble_tokens=100, effective_window=5000)
        result = wrapped({"messages": messages})

        # 结果应该包含 preamble + 裁剪后的消息
        assert result[0] == preamble[0]  # preamble 保留

        trimmed_messages = result[1:]  # 去掉 preamble
        assert len(trimmed_messages) < len(messages)  # 消息被裁剪

        # 裁剪后首条消息应为 human 类型（start_on="human"）
        if trimmed_messages:
            assert isinstance(trimmed_messages[0], HumanMessage)

    def test_preserves_short_history(self) -> None:
        """验证 _wrap_prompt 在历史未超出预算时不裁剪"""
        from apps.graph.agent import _wrap_prompt

        preamble = [SystemMessage(content="You are a helpful assistant.")]
        messages = [
            HumanMessage(content="你好"),
            AIMessage(content="你好！有什么可以帮你的吗？"),
            HumanMessage(content="今天天气怎么样？"),
            AIMessage(content="我无法获取实时天气信息。"),
        ]

        # 足够大的窗口
        wrapped = _wrap_prompt(preamble, preamble_tokens=100, effective_window=128000)
        result = wrapped({"messages": messages})

        # preamble + 全部 4 条消息
        assert len(result) == 5
        assert result[0] == preamble[0]
        assert result[1:] == messages

    def test_minimum_budget_guarantee(self) -> None:
        """验证即使 preamble 很大，也至少保留 2000 token 预算"""
        from apps.graph.agent import RESPONSE_RESERVE, _wrap_prompt

        preamble = [SystemMessage(content="x")]
        messages = [
            HumanMessage(content="你好"),
            AIMessage(content="你好！"),
        ]

        # preamble_tokens 接近 effective_window，使 history_budget 为负
        wrapped = _wrap_prompt(
            preamble, preamble_tokens=120000, effective_window=128000
        )
        result = wrapped({"messages": messages})

        # 消息仍应被保留（因为 max(budget, 2000) 兜底）
        assert len(result) >= 2  # 至少 preamble + 部分消息

    def test_none_prompt_returns_none(self) -> None:
        """验证 prompt=None 时返回 None"""
        from apps.graph.agent import _wrap_prompt

        assert _wrap_prompt(None) is None
        assert _wrap_prompt(None, preamble_tokens=100, effective_window=5000) is None
