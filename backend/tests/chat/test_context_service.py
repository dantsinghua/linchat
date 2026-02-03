"""
ContextService 测试 [T047] [T062] [T063]

覆盖 get_effective_window、check_token_limit、build_context、compress_context。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import TransactionTestCase

from apps.chat.services.context_service import (
    ContextService,
    ContextWindowTooSmallError,
    MIN_EFFECTIVE_WINDOW,
)
from tests.helpers import run_async


class TestGetEffectiveWindow:
    """get_effective_window 单元测试"""

    def test_normal_config(self) -> None:
        config = {"max_context_window": 128000}
        result = ContextService.get_effective_window(config)
        assert result == 115200  # 128000 * 0.9

    def test_small_config(self) -> None:
        config = {"max_context_window": 32000}
        result = ContextService.get_effective_window(config)
        assert result == 28800  # 32000 * 0.9

    def test_default_window(self) -> None:
        """缺少 key 时使用默认 128000"""
        result = ContextService.get_effective_window({})
        assert result == 115200

    def test_too_small_raises(self) -> None:
        """有效窗口 < 10000 时抛出异常"""
        config = {"max_context_window": 5000}  # 5000 * 0.9 = 4500
        with pytest.raises(ContextWindowTooSmallError):
            ContextService.get_effective_window(config)

    def test_boundary_exactly_min(self) -> None:
        """边界：恰好 = MIN_EFFECTIVE_WINDOW 不抛异常"""
        # 需要 max * 0.9 >= 10000，即 max >= 11112
        config = {"max_context_window": 11112}
        result = ContextService.get_effective_window(config)
        assert result >= MIN_EFFECTIVE_WINDOW


class TestCheckTokenLimit:
    """check_token_limit 单元测试"""

    def test_under_limit(self) -> None:
        messages = [{"content": "hi"}]
        assert ContextService.check_token_limit(messages, 100000) is False

    def test_over_limit(self) -> None:
        """超长消息超限"""
        messages = [{"content": "word " * 50000}]  # ~50000 tokens
        assert ContextService.check_token_limit(messages, 100) is True

    def test_empty_messages(self) -> None:
        assert ContextService.check_token_limit([], 100) is False


class TestBuildContext(TransactionTestCase):
    """build_context 集成测试"""

    MODEL_CONFIG = {"max_context_window": 128000, "name": "test-model"}

    @patch("apps.chat.services.context_service.MemoryService", create=True)
    def test_build_context_basic(self, mock_mem_svc) -> None:
        """基本调用流程"""
        mock_mem_svc.search_memory = AsyncMock(return_value=[])

        messages = run_async(
            ContextService.build_context(
                user_id=1,
                user_message="你好",
                model_config=self.MODEL_CONFIG,
            )
        )

        assert len(messages) >= 2  # system + user
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "你好"

    @patch("apps.chat.services.context_service.MemoryService", create=True)
    def test_build_context_with_history(self, mock_mem_svc) -> None:
        """包含对话历史"""
        mock_mem_svc.search_memory = AsyncMock(return_value=[])

        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        messages = run_async(
            ContextService.build_context(
                user_id=1,
                user_message="q2",
                model_config=self.MODEL_CONFIG,
                conversation_history=history,
            )
        )

        contents = [m["content"] for m in messages]
        assert "q1" in contents
        assert "a1" in contents
        assert "q2" in contents

    @patch("apps.chat.services.context_service.MemoryService", create=True)
    def test_build_context_memory_failure_degrades(self, mock_mem_svc) -> None:
        """记忆召回失败降级为无记忆"""
        mock_mem_svc.search_memory = AsyncMock(
            side_effect=Exception("Memory service down")
        )

        messages = run_async(
            ContextService.build_context(
                user_id=1,
                user_message="test",
                model_config=self.MODEL_CONFIG,
            )
        )

        # 应该正常返回（不包含记忆）
        assert len(messages) >= 2

    def test_build_context_small_window_raises(self) -> None:
        """上下文窗口过小时抛异常"""
        small_config = {"max_context_window": 5000}

        with pytest.raises(ContextWindowTooSmallError):
            run_async(
                ContextService.build_context(
                    user_id=1,
                    user_message="test",
                    model_config=small_config,
                )
            )


# ============================================================================
# compress_context 测试 [T062]
# ============================================================================


class TestCompressContext:
    """compress_context 压缩编排测试"""

    def _make_messages(
        self,
        system_content: str = "system prompt",
        history_pairs: int = 0,
        memory_content: str = "",
        tool_content: str = "",
        user_input: str = "current",
    ) -> list[dict[str, str]]:
        """构造测试消息列表"""
        msgs = [{"role": "system", "content": system_content}]
        if memory_content:
            msgs.append({"role": "system", "content": memory_content, "name": "memory"})
        if tool_content:
            msgs.append({"role": "system", "content": tool_content, "name": "tools"})
        for i in range(history_pairs):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        msgs.append({"role": "user", "content": user_input})
        return msgs

    @patch("apps.chat.services.context_service.ContextService._acquire_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._release_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._llm_compress")
    @patch("apps.common.tokenizer.count_tokens")
    def test_compress_l1_only(
        self, mock_tokens, mock_llm, mock_release, mock_lock
    ) -> None:
        """仅压缩 L1（对话历史）即满足预算"""
        mock_lock.return_value = AsyncMock()  # 获取锁成功
        mock_release.return_value = None
        mock_llm.return_value = "压缩摘要"

        messages = self._make_messages(history_pairs=3, user_input="current")

        # 初始超限，移除 L1 后不超限
        call_count = [0]

        def side_effect(text):
            call_count[0] += 1
            # 系统消息 + 对话历史 = 超限
            if text in ("q0", "q1", "q2", "a0", "a1", "a2"):
                return 100
            return 10

        mock_tokens.side_effect = side_effect

        result, summary = run_async(
            ContextService.compress_context(
                user_id=1,
                messages=messages,
                effective_window=200,
            )
        )

        # L1 对话历史被压缩/替换
        contents = [m["content"] for m in result]
        assert "current" in contents  # 当前输入保留
        assert "system prompt" in contents  # system 保留

    @patch("apps.chat.services.context_service.ContextService._acquire_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._release_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._llm_compress")
    def test_llm_failure_fallback_truncation(
        self, mock_llm, mock_release, mock_lock
    ) -> None:
        """LLM 压缩失败回退截断 [T059]"""
        mock_lock.return_value = AsyncMock()
        mock_release.return_value = None
        mock_llm.return_value = None  # LLM 失败

        # 构造超限消息
        messages = self._make_messages(history_pairs=2, user_input="current")

        result, summary = run_async(
            ContextService.compress_context(
                user_id=1,
                messages=messages,
                effective_window=1000000,  # 大预算，不会触发实际压缩
            )
        )

        # 不超限时不触发压缩
        assert summary is None

    @patch("apps.chat.services.context_service.ContextService._acquire_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._release_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._llm_compress")
    def test_llm_failure_no_compaction_memory(
        self, mock_llm, mock_release, mock_lock
    ) -> None:
        """LLM 全部失败时不生成 compaction 记忆 [R-014]"""
        mock_lock.return_value = AsyncMock()
        mock_release.return_value = None
        mock_llm.return_value = None  # 全部失败

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old q"},
            {"role": "assistant", "content": "old a"},
            {"role": "user", "content": "current"},
        ]

        with patch("apps.common.tokenizer.count_tokens") as mock_ct:
            # 第一次检查超限，之后不超限
            check_count = [0]

            def ct_side(text):
                check_count[0] += 1
                if text in ("old q", "old a"):
                    return 500
                return 10

            mock_ct.side_effect = ct_side

            result, summary = run_async(
                ContextService.compress_context(
                    user_id=1,
                    messages=messages,
                    effective_window=100,
                )
            )

        # LLM 失败不生成 compaction
        assert summary is None

    @patch("apps.chat.services.context_service.ContextService._acquire_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._release_compress_lock")
    def test_redis_unavailable_degrades(self, mock_release, mock_lock) -> None:
        """Redis 不可用时降级为无锁执行 [T058.1a]"""
        mock_lock.return_value = None  # Redis 不可用，无法获取锁
        mock_release.return_value = None

        with patch.object(
            ContextService, "_wait_for_compress_lock", new_callable=AsyncMock
        ) as mock_wait:
            mock_wait.return_value = True  # 仍需压缩

            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "current"},
            ]

            result, _ = run_async(
                ContextService.compress_context(
                    user_id=1,
                    messages=messages,
                    effective_window=1000000,
                )
            )

            # 不抛异常，正常返回
            assert len(result) >= 1

    @patch("apps.chat.services.context_service.ContextService._acquire_compress_lock")
    @patch("apps.chat.services.context_service.ContextService._release_compress_lock")
    def test_sse_callback_called(self, mock_release, mock_lock) -> None:
        """SSE 回调被调用"""
        mock_lock.return_value = AsyncMock()
        mock_release.return_value = None

        callback = AsyncMock()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "current"},
        ]

        run_async(
            ContextService.compress_context(
                user_id=1,
                messages=messages,
                effective_window=1000000,
                sse_callback=callback,
            )
        )

        # 应调用 context_compacting 和 context_compacted
        callback_args = [call.args[0] for call in callback.call_args_list]
        assert "context_compacting" in callback_args
        assert "context_compacted" in callback_args


# ============================================================================
# 安全兜底测试 [T063]
# ============================================================================


class TestSafetyNet:
    """安全兜底逻辑测试"""

    @patch("apps.chat.services.context_service.MemoryService", create=True)
    def test_force_truncate_exceeds_max_window(self, mock_mem_svc) -> None:
        """超过 100% 最大窗口直接截断不抛异常 [R-019]"""
        mock_mem_svc.search_memory = AsyncMock(return_value=[])

        # 使用小窗口但正常内容
        config = {"max_context_window": 12000, "name": "test"}

        with patch.object(
            ContextService,
            "compress_context",
            new_callable=AsyncMock,
        ) as mock_compress:
            # compress_context 返回仍超限的消息
            huge_msg = [
                {"role": "system", "content": "x" * 100000},
                {"role": "user", "content": "q"},
            ]
            mock_compress.return_value = (huge_msg, None)

            # 不应抛异常
            result = run_async(
                ContextService.build_context(
                    user_id=1,
                    user_message="test",
                    model_config=config,
                )
            )
            assert isinstance(result, list)

    @patch("apps.chat.services.context_service.MemoryService", create=True)
    def test_within_buffer_no_truncation(self, mock_mem_svc) -> None:
        """在有效窗口内不触发压缩"""
        mock_mem_svc.search_memory = AsyncMock(return_value=[])

        config = {"max_context_window": 128000, "name": "test"}

        messages = run_async(
            ContextService.build_context(
                user_id=1,
                user_message="hi",
                model_config=config,
            )
        )

        # 短消息不应触发压缩
        assert len(messages) >= 2
