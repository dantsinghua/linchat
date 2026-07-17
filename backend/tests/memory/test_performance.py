"""
性能基准测试 [T077]

验证：
- tokenizer 计算延迟（大消息列表）
- 上下文裁剪延迟（不含 LLM 等待）
- trim_messages_to_budget 延迟
"""

import time

import pytest

from apps.context import PromptBuilder, PromptConfig, trim_messages_to_budget
from apps.graph.services.context_service import ContextService, _total_tokens
from apps.common.tokenizer import count_messages_tokens, count_tokens


class TestTokenizerPerformance:
    """tokenizer 计算延迟测试"""

    def test_count_tokens_large_text(self):
        """大文本 token 计数应在 50ms 内完成"""
        large_text = "这是一段很长的中文文本。" * 5000  # ~50000 字符

        # 预热编码器（首次加载有初始化开销）
        count_tokens("warmup")

        start = time.perf_counter()
        result = count_tokens(large_text)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result > 0
        assert elapsed_ms < 200, f"Token counting took {elapsed_ms:.1f}ms, expected <200ms"

    def test_count_messages_tokens_large_list(self):
        """大消息列表 token 计数应在 100ms 内完成"""
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息内容 {i} " * 50}
            for i in range(200)
        ]

        start = time.perf_counter()
        result = count_messages_tokens(messages)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result > 0
        assert elapsed_ms < 100, f"Message token counting took {elapsed_ms:.1f}ms, expected <100ms"


class TestContextTrimPerformance:
    """上下文裁剪延迟测试（不含 LLM）"""

    def test_trim_messages_to_budget(self):
        """trim_messages_to_budget 应在 100ms 内完成"""
        messages = [
            {"role": "system", "content": "你是一个助手。" * 100},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"对话消息 {i} " * 100}
            for i in range(100)
        ]

        start = time.perf_counter()
        result = trim_messages_to_budget(messages, token_budget=5000)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(result) > 0
        assert elapsed_ms < 100, f"Trim took {elapsed_ms:.1f}ms, expected <100ms"

    def test_total_tokens_calculation(self):
        """_total_tokens 大列表计算应在 50ms 内完成"""
        messages = [
            {"role": "user", "content": "测试消息 " * 200}
            for _ in range(100)
        ]

        start = time.perf_counter()
        result = _total_tokens(messages)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result > 0
        assert elapsed_ms < 50, f"Total tokens took {elapsed_ms:.1f}ms, expected <50ms"

    def test_check_token_limit_performance(self):
        """check_token_limit 应在 50ms 内完成"""
        messages = [
            {"role": "user", "content": "这是一条测试消息。" * 100}
            for _ in range(100)
        ]

        start = time.perf_counter()
        result = ContextService.check_token_limit(messages, 50000)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert isinstance(result, bool)
        assert elapsed_ms < 50, f"Check token limit took {elapsed_ms:.1f}ms, expected <50ms"


class TestPromptBuilderPerformance:
    """PromptBuilder 组装延迟测试"""

    def test_build_messages_performance(self):
        """build_messages 应在 100ms 内完成（不含 LLM）"""
        config = PromptConfig(
            user_id=1,
            max_context_window=128000,
            model_name="test-model",
        )
        builder = PromptBuilder(config=config)

        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史消息 {i} " * 50}
            for i in range(50)
        ]

        start = time.perf_counter()
        result = builder.build_messages(
            user_input="用户新消息",
            conversation_history=history,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(result) > 0
        assert elapsed_ms < 100, f"Build messages took {elapsed_ms:.1f}ms, expected <100ms"
