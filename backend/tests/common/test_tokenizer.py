"""
tokenizer 精度测试 [T045]

覆盖 count_tokens 和 count_messages_tokens 的核心场景：
空输入、英文、中文、混合文本、编码器异常降级。
"""

from unittest.mock import patch

import pytest

from apps.common.tokenizer import count_messages_tokens, count_tokens


class TestCountTokens:
    """count_tokens 单元测试"""

    def test_empty_string(self) -> None:
        assert count_tokens("") == 0

    def test_none_like_empty(self) -> None:
        """空字符串返回 0"""
        assert count_tokens("") == 0

    def test_english_text(self) -> None:
        """英文文本 token 计数合理"""
        tokens = count_tokens("Hello, world!")
        assert 2 <= tokens <= 5  # cl100k_base: ~4 tokens

    def test_chinese_text(self) -> None:
        """中文文本 token 计数（中文字符通常 1-2 token/字）"""
        tokens = count_tokens("你好世界")
        assert tokens >= 2  # 至少 2 个 token

    def test_mixed_text(self) -> None:
        """中英混合"""
        tokens = count_tokens("Hello 你好 World 世界")
        assert tokens >= 4

    def test_long_text(self) -> None:
        """较长文本不出错"""
        text = "测试文本 " * 1000
        tokens = count_tokens(text)
        assert tokens > 0

    def test_special_characters(self) -> None:
        """特殊字符"""
        tokens = count_tokens("🎉🚀✨")
        assert tokens > 0

    def test_code_snippet(self) -> None:
        """代码片段"""
        code = "def hello():\n    print('Hello, world!')\n"
        tokens = count_tokens(code)
        assert tokens >= 5

    @patch("apps.common.tokenizer._get_encoder")
    def test_fallback_on_error(self, mock_encoder) -> None:
        """编码器异常时降级为字符估算 len//4"""
        mock_encoder.side_effect = Exception("encoder broken")
        tokens = count_tokens("12345678")  # len=8, 8//4=2
        assert tokens == 2


class TestCountMessagesTokens:
    """count_messages_tokens 单元测试"""

    def test_empty_list(self) -> None:
        assert count_messages_tokens([]) == 0

    def test_single_message(self) -> None:
        """单条消息：4(开销) + role_tokens + content_tokens + 2(回复)"""
        result = count_messages_tokens(
            [{"role": "user", "content": "Hello"}]
        )
        # "user" ~1 token, "Hello" ~1 token, overhead=4, reply=2 → ~8
        assert result >= 6

    def test_multiple_messages(self) -> None:
        """多条消息"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        result = count_messages_tokens(messages)
        assert result > 10  # 2×4 overhead + tokens + 2 reply

    def test_empty_content(self) -> None:
        """content 为空字符串"""
        result = count_messages_tokens(
            [{"role": "user", "content": ""}]
        )
        # 4(overhead) + role_tokens + 0 + 2(reply)
        assert result >= 6

    def test_missing_content_key(self) -> None:
        """缺少 content 键"""
        result = count_messages_tokens(
            [{"role": "user"}]
        )
        assert result >= 6

    @patch("apps.common.tokenizer._get_encoder")
    def test_fallback_on_error(self, mock_encoder) -> None:
        """编码器异常时降级"""
        mock_encoder.side_effect = Exception("encoder broken")
        result = count_messages_tokens(
            [{"role": "user", "content": "12345678"}]
        )
        # fallback: len("12345678")//4 + 4 + 2 = 2 + 4 + 2 = 8
        assert result == 8
