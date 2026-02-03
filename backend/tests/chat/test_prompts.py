"""
Prompt 模板系统测试 [T046]

覆盖 PromptBuilder、PromptModule、TrimLevel、trim_messages_to_budget
的核心场景。
"""

from unittest.mock import patch

import pytest

from apps.graph.prompts import (
    BASE_SYSTEM_PROMPT,
    BEHAVIOR_GUIDELINES,
    COMPACTION_PROMPT_TEMPLATE,
    CRONMEM_PROMPT_TEMPLATE,
    DAILY_SUMMARY_PROMPT_TEMPLATE,
    MONTHLY_SUMMARY_PROMPT_TEMPLATE,
    MEMORY_CONTEXT_HEADER,
    PromptBuilder,
    PromptConfig,
    PromptMessage,
    PromptModule,
    MessageRole,
    RetrievedMemory,
    TaggedMessage,
    ToolDefinition,
    TrimLevel,
    get_module_prompt,
    register_custom_module,
    trim_messages_to_budget,
)


# ============================================================================
# PromptConfig 测试
# ============================================================================


class TestPromptConfig:
    def test_defaults(self) -> None:
        config = PromptConfig()
        assert config.max_context_window == 128000
        assert config.effective_window_ratio == 0.9
        assert config.keep_recent_rounds == 2
        assert config.max_memory_items == 5

    def test_effective_window(self) -> None:
        config = PromptConfig(max_context_window=100000)
        assert config.effective_window == 90000

    def test_custom_config(self) -> None:
        config = PromptConfig(
            user_id=42,
            max_context_window=200000,
            keep_recent_rounds=5,
        )
        assert config.user_id == 42
        assert config.effective_window == 180000
        assert config.keep_recent_rounds == 5


# ============================================================================
# PromptBuilder 模块管理测试
# ============================================================================


class TestPromptBuilderModules:
    def test_default_modules(self) -> None:
        builder = PromptBuilder()
        # BASE 和 REASONING 默认启用
        assert PromptModule.BASE in builder._enabled_modules
        assert PromptModule.REASONING in builder._enabled_modules

    def test_enable_module(self) -> None:
        builder = PromptBuilder()
        builder.enable_module(PromptModule.TOOL_USAGE)
        assert PromptModule.TOOL_USAGE in builder._enabled_modules

    def test_enable_same_module_twice(self) -> None:
        builder = PromptBuilder()
        builder.enable_module(PromptModule.CODE_ASSIST)
        builder.enable_module(PromptModule.CODE_ASSIST)
        count = builder._enabled_modules.count(PromptModule.CODE_ASSIST)
        assert count == 1

    def test_disable_module(self) -> None:
        builder = PromptBuilder()
        builder.disable_module(PromptModule.REASONING)
        assert PromptModule.REASONING not in builder._enabled_modules

    def test_chaining(self) -> None:
        builder = (
            PromptBuilder()
            .enable_module(PromptModule.TOOL_USAGE)
            .disable_module(PromptModule.REASONING)
            .add_system_instruction("test instruction")
        )
        assert PromptModule.TOOL_USAGE in builder._enabled_modules
        assert PromptModule.REASONING not in builder._enabled_modules
        assert "test instruction" in builder._extra_system_instructions


# ============================================================================
# PromptBuilder 组件构建测试
# ============================================================================


class TestPromptBuilderComponents:
    def test_build_system_prompt(self) -> None:
        builder = PromptBuilder(config=PromptConfig(user_timezone="UTC"))
        prompt = builder.build_system_prompt()
        assert "LinChat 智能助手" in prompt
        assert "UTC" in prompt
        # 包含行为规范（BASE 模块）
        assert "回复规范" in prompt
        # 包含推理规范（REASONING 模块）
        assert "结构化思考" in prompt

    def test_build_system_prompt_with_extra_instruction(self) -> None:
        builder = PromptBuilder()
        builder.add_system_instruction("请用日语回答")
        prompt = builder.build_system_prompt()
        assert "请用日语回答" in prompt

    def test_build_memory_block_none(self) -> None:
        builder = PromptBuilder()
        result = builder.build_memory_block(None)
        assert result is None

    def test_build_memory_block_empty(self) -> None:
        builder = PromptBuilder()
        result = builder.build_memory_block([])
        assert result is None

    def test_build_memory_block_with_memories(self) -> None:
        builder = PromptBuilder(config=PromptConfig(max_memory_items=3))
        memories = [
            RetrievedMemory(content="喜欢 Python", memory_type="memory", relevance_score=0.9),
            RetrievedMemory(content="住在北京", memory_type="memory", relevance_score=0.7),
        ]
        result = builder.build_memory_block(memories)
        assert result is not None
        assert "喜欢 Python" in result
        assert "住在北京" in result
        assert "用户相关记忆" in result

    def test_build_memory_block_sorted_by_relevance(self) -> None:
        builder = PromptBuilder(config=PromptConfig(max_memory_items=5))
        memories = [
            RetrievedMemory(content="low", relevance_score=0.1),
            RetrievedMemory(content="high", relevance_score=0.9),
            RetrievedMemory(content="mid", relevance_score=0.5),
        ]
        result = builder.build_memory_block(memories)
        # high 应在 low 之前
        assert result.index("high") < result.index("low")

    def test_build_memory_block_truncated(self) -> None:
        """超过 max_memory_items 时截取"""
        builder = PromptBuilder(config=PromptConfig(max_memory_items=2))
        memories = [
            RetrievedMemory(content=f"item{i}", relevance_score=0.9 - i * 0.1)
            for i in range(5)
        ]
        result = builder.build_memory_block(memories)
        # 只保留 top 2
        assert "item0" in result
        assert "item1" in result
        assert "item4" not in result

    def test_build_compaction_block_none(self) -> None:
        builder = PromptBuilder()
        assert builder.build_compaction_block(None) is None

    def test_build_compaction_block(self) -> None:
        builder = PromptBuilder()
        result = builder.build_compaction_block("摘要内容")
        assert "摘要内容" in result
        assert "对话摘要" in result

    def test_build_tool_context_none(self) -> None:
        builder = PromptBuilder()
        assert builder.build_tool_context(None) is None

    def test_build_tool_context(self) -> None:
        builder = PromptBuilder()
        tools = [
            ToolDefinition(
                name="search",
                description="搜索工具",
                parameters={"query": {"type": "string", "description": "搜索词", "required": True}},
            ),
        ]
        result = builder.build_tool_context(tools)
        assert "search" in result
        assert "搜索工具" in result
        assert "query" in result

    def test_build_tool_context_disabled_tool(self) -> None:
        builder = PromptBuilder()
        tools = [
            ToolDefinition(name="disabled", description="已禁用", enabled=False),
        ]
        result = builder.build_tool_context(tools)
        assert result is None

    def test_build_conversation_history_none(self) -> None:
        builder = PromptBuilder()
        assert builder.build_conversation_history(None) == []

    def test_build_conversation_history(self) -> None:
        builder = PromptBuilder(config=PromptConfig(keep_recent_rounds=2))
        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
        ]
        result = builder.build_conversation_history(history)
        # keep_recent_rounds=2 → 4 条消息
        assert len(result) == 4
        assert result[0].content == "q2"
        assert result[-1].content == "a3"


# ============================================================================
# PromptBuilder 主组装方法测试
# ============================================================================


class TestPromptBuilderBuild:
    def test_build_messages_basic(self) -> None:
        builder = PromptBuilder()
        messages = builder.build_messages(user_input="你好")
        # 至少包含 system + user
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "你好"

    def test_build_messages_with_history(self) -> None:
        builder = PromptBuilder()
        history = [
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ]
        messages = builder.build_messages(
            user_input="新问题",
            conversation_history=history,
        )
        contents = [m["content"] for m in messages]
        assert "之前的问题" in contents
        assert "之前的回答" in contents
        assert messages[-1]["content"] == "新问题"

    def test_build_messages_with_memories(self) -> None:
        builder = PromptBuilder()
        memories = [
            RetrievedMemory(content="用户喜欢咖啡", relevance_score=0.8),
        ]
        messages = builder.build_messages(
            user_input="推荐饮品",
            retrieved_memories=memories,
        )
        all_content = " ".join(m["content"] for m in messages)
        assert "用户喜欢咖啡" in all_content

    def test_build_messages_with_compaction(self) -> None:
        builder = PromptBuilder()
        messages = builder.build_messages(
            user_input="继续",
            compaction_summary="之前讨论了 Python 项目架构",
        )
        all_content = " ".join(m["content"] for m in messages)
        assert "Python 项目架构" in all_content

    def test_build_preamble(self) -> None:
        """build_preamble 返回 SystemMessage 列表"""
        from langchain_core.messages import SystemMessage

        builder = PromptBuilder()
        preamble = builder.build_preamble(
            retrieved_memories=[
                RetrievedMemory(content="test memory", relevance_score=0.8),
            ],
        )
        assert len(preamble) >= 2  # system prompt + memory
        assert all(isinstance(m, SystemMessage) for m in preamble)

    def test_build_preamble_no_extras(self) -> None:
        """无记忆/摘要/工具时 preamble 只有一条"""
        from langchain_core.messages import SystemMessage

        builder = PromptBuilder()
        preamble = builder.build_preamble()
        assert len(preamble) == 1
        assert isinstance(preamble[0], SystemMessage)


# ============================================================================
# TrimLevel 和 trim_messages_to_budget 测试
# ============================================================================


class TestTrimLevel:
    def test_ordering(self) -> None:
        assert TrimLevel.PROTECTED < TrimLevel.FIRST
        assert TrimLevel.FIRST < TrimLevel.SECOND
        assert TrimLevel.SECOND < TrimLevel.LAST

    def test_values(self) -> None:
        assert TrimLevel.PROTECTED == 0
        assert TrimLevel.FIRST == 1
        assert TrimLevel.SECOND == 2
        assert TrimLevel.LAST == 3


class TestTrimMessages:
    def test_under_budget_no_trim(self) -> None:
        """总 token 在预算内不裁剪"""
        messages = [
            {"role": "system", "content": "short"},
            {"role": "user", "content": "hi"},
        ]
        result = trim_messages_to_budget(messages, 100000)
        assert len(result) == 2

    @patch("apps.common.tokenizer.count_tokens")
    def test_trim_conversation_first(self, mock_count) -> None:
        """L1 对话历史最先被裁剪"""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current input"},
        ]
        # system=100, old_q=100, old_a=100, current=100
        token_values = {
            "system prompt": 100,
            "old question": 100,
            "old answer": 100,
            "current input": 100,
        }
        mock_count.side_effect = lambda t: token_values.get(t, 10)

        result = trim_messages_to_budget(messages, 250)
        # 应移除对话历史（old question + old answer），保留 system + current input
        roles = [m["role"] for m in result]
        contents = [m["content"] for m in result]
        assert "system prompt" in contents
        assert "current input" in contents
        # 旧对话应被移除
        assert "old question" not in contents

    @patch("apps.common.tokenizer.count_tokens")
    def test_protected_never_trimmed(self, mock_count) -> None:
        """PROTECTED 级别消息永远保留"""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "input"},
        ]
        mock_count.return_value = 100

        result = trim_messages_to_budget(messages, 50)
        # 即使预算不够，system 和最后一条 user 也保留
        assert len(result) == 2

    @patch("apps.common.tokenizer.count_tokens")
    def test_trim_order_l1_l2_l3(self, mock_count) -> None:
        """裁剪顺序：L1(对话) → L2(工具) → L3(记忆)"""
        messages = [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "memory block", "name": "memory"},
            {"role": "system", "content": "tool block", "name": "tools"},
            {"role": "user", "content": "hist q"},
            {"role": "assistant", "content": "hist a"},
            {"role": "user", "content": "current"},
        ]
        token_values = {
            "base": 50,
            "memory block": 50,
            "tool block": 50,
            "hist q": 50,
            "hist a": 50,
            "current": 50,
        }
        mock_count.side_effect = lambda t: token_values.get(t, 10)

        # budget=200 → total=300, 需裁掉 100
        # L1 先被裁掉 (hist q + hist a = 100)
        result = trim_messages_to_budget(messages, 200)
        contents = [m["content"] for m in result]
        assert "hist q" not in contents
        assert "hist a" not in contents
        assert "memory block" in contents
        assert "tool block" in contents


# ============================================================================
# Prompt 模板完整性测试
# ============================================================================


class TestPromptTemplates:
    def test_compaction_template_has_placeholder(self) -> None:
        assert "{conversation_text}" in COMPACTION_PROMPT_TEMPLATE

    def test_daily_summary_template_has_placeholders(self) -> None:
        assert "{conversation_text}" in DAILY_SUMMARY_PROMPT_TEMPLATE
        assert "{date}" in DAILY_SUMMARY_PROMPT_TEMPLATE

    def test_monthly_summary_template_has_placeholders(self) -> None:
        assert "{daily_summaries}" in MONTHLY_SUMMARY_PROMPT_TEMPLATE
        assert "{year_month}" in MONTHLY_SUMMARY_PROMPT_TEMPLATE

    def test_cronmem_template_has_placeholders(self) -> None:
        assert "{existing_memories}" in CRONMEM_PROMPT_TEMPLATE
        assert "{conversation_text}" in CRONMEM_PROMPT_TEMPLATE

    def test_cronmem_template_json_format(self) -> None:
        """CronMem 模板指定了 JSON 输出格式"""
        assert "facts" in CRONMEM_PROMPT_TEMPLATE
        assert "json" in CRONMEM_PROMPT_TEMPLATE.lower()


# ============================================================================
# 模块注册测试
# ============================================================================


class TestModuleRegistry:
    def test_get_base_module(self) -> None:
        prompt = get_module_prompt(PromptModule.BASE)
        assert "回复规范" in prompt

    def test_get_nonexistent_module(self) -> None:
        prompt = get_module_prompt(PromptModule.CUSTOM)
        assert prompt == ""

    def test_register_custom_module(self) -> None:
        register_custom_module("my_module", "自定义内容")
        builder = PromptBuilder()
        builder.enable_custom_module("my_module")
        prompt = builder.build_system_prompt()
        assert "自定义内容" in prompt
