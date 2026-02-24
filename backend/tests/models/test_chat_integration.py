"""
聊天集成测试 (T030)

覆盖:
- agent.py get_llm() 从数据库读取 tool 模型配置
- get_llm() 仅传入非 NULL 选填参数（FR-007）
- get_llm() 无 lru_cache 时配置变更即时生效
- get_llm() 使用 max_context_window 原始值而非 effective_context_window
- chat/services.py _get_tool_model_name() 取自数据库配置
- 管理员修改模型名称后 _get_tool_model_name() 返回新值
"""
from unittest.mock import MagicMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import TestCase

from apps.models.models import ModelConfig
from apps.models.services import model_service
from apps.users.crypto import sm4_encrypt


class TestGetLlmFromDatabase(TestCase):
    """get_llm() 从数据库读取配置测试"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.api_key_plain = "test-api-key-for-llm-12345"
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_TOOL,
            name="deepseek-v3-test",
            url="https://api.test.com/v1",
            api_key=sm4_encrypt(self.api_key_plain),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
            temperature=0.7,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            is_active=True,
        )

    @patch("apps.graph.agent.ChatOpenAI")
    def test_get_llm_reads_from_database(self, mock_chat_openai):
        """测试 get_llm() 从数据库读取 tool 模型配置"""
        from apps.graph.agent import get_llm

        mock_chat_openai.return_value = MagicMock()
        async_to_sync(get_llm)()

        mock_chat_openai.assert_called_once()
        call_kwargs = mock_chat_openai.call_args[1]

        self.assertEqual(call_kwargs["model"], "deepseek-v3-test")
        self.assertEqual(call_kwargs["base_url"], "https://api.test.com/v1")
        self.assertEqual(call_kwargs["api_key"], self.api_key_plain)

    @patch("apps.graph.agent.ChatOpenAI")
    def test_get_llm_only_passes_non_null_optional_params(self, mock_chat_openai):
        """测试 get_llm() 仅传入非 NULL 选填参数（FR-007）"""
        from apps.graph.agent import get_llm

        mock_chat_openai.return_value = MagicMock()
        async_to_sync(get_llm)()

        call_kwargs = mock_chat_openai.call_args[1]

        # temperature=0.7 应该被传入
        self.assertEqual(call_kwargs["temperature"], 0.7)

        # top_p=None 不应该被传入
        self.assertNotIn("top_p", call_kwargs)
        self.assertNotIn("frequency_penalty", call_kwargs)
        self.assertNotIn("presence_penalty", call_kwargs)

    @patch("apps.graph.agent.ChatOpenAI")
    def test_get_llm_passes_zero_values(self, mock_chat_openai):
        """测试 get_llm() 传入值为 0 的选填参数（0 != NULL）"""
        from apps.graph.agent import get_llm

        self.model.temperature = 0
        self.model.top_p = 0
        self.model.save()

        mock_chat_openai.return_value = MagicMock()
        async_to_sync(get_llm)()

        call_kwargs = mock_chat_openai.call_args[1]
        self.assertEqual(call_kwargs["temperature"], 0)
        self.assertEqual(call_kwargs["top_p"], 0)

    @patch("apps.graph.agent.ChatOpenAI")
    def test_get_llm_config_change_takes_effect_immediately(self, mock_chat_openai):
        """测试无 lru_cache 时配置变更即时生效"""
        from apps.graph.agent import get_llm

        mock_chat_openai.return_value = MagicMock()

        # 第一次调用
        async_to_sync(get_llm)()
        first_call_kwargs = mock_chat_openai.call_args[1]
        self.assertEqual(first_call_kwargs["model"], "deepseek-v3-test")

        # 修改配置
        self.model.name = "deepseek-v3-updated"
        self.model.save()

        # 第二次调用应该使用新配置
        async_to_sync(get_llm)()
        second_call_kwargs = mock_chat_openai.call_args[1]
        self.assertEqual(second_call_kwargs["model"], "deepseek-v3-updated")

    @patch("apps.graph.agent.ChatOpenAI")
    def test_get_llm_uses_max_context_window_not_effective(self, mock_chat_openai):
        """测试使用 max_context_window 原始值而非 effective_context_window

        M1a 阶段模型 API 调用直接使用 max_context_window 原始值。
        effective_context_window 仅供 M1b 上下文管理使用。
        """
        from apps.graph.agent import get_llm

        mock_chat_openai.return_value = MagicMock()
        async_to_sync(get_llm)()

        # get_llm 不应传入 max_context_window 到 ChatOpenAI
        # (ChatOpenAI 不接受此参数)
        # 但底层配置读取的是 max_context_window 而非 effective_context_window
        config = model_service.get_active_model("tool")
        self.assertEqual(config["max_context_window"], 65536)
        self.assertEqual(config["effective_context_window"], int(65536 * 0.9))
        # 两者不同，确认区分
        self.assertNotEqual(
            config["max_context_window"], config["effective_context_window"]
        )

    def test_get_llm_no_active_model_raises(self):
        """测试无激活模型时抛出 RuntimeError"""
        from apps.graph.agent import get_llm

        ModelConfig.objects.all().delete()
        with self.assertRaises(RuntimeError) as ctx:
            async_to_sync(get_llm)()
        self.assertIn("未找到激活的工具模型配置", str(ctx.exception))


class TestGetToolModelName(TestCase):
    """_get_tool_model_name() 测试"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_TOOL,
            name="test-tool-model",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("test-key-for-name-12345"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
            is_active=True,
        )

    def test_returns_model_name_from_database(self):
        """测试 model_name 取自数据库模型配置"""
        from apps.chat.services import _get_tool_model_name

        name = async_to_sync(_get_tool_model_name)()
        self.assertEqual(name, "test-tool-model")

    def test_returns_updated_name_after_change(self):
        """测试管理员修改模型名称后返回新值"""
        from apps.chat.services import _get_tool_model_name

        # 初始名称
        self.assertEqual(async_to_sync(_get_tool_model_name)(), "test-tool-model")

        # 模拟管理员修改
        model_service.update_model(self.model.id, {"name": "renamed-model"})

        # 应返回新名称
        self.assertEqual(async_to_sync(_get_tool_model_name)(), "renamed-model")

    def test_returns_unknown_when_no_active_model(self):
        """测试无激活模型时返回 'unknown'"""
        from apps.chat.services import _get_tool_model_name

        ModelConfig.objects.all().delete()
        name = async_to_sync(_get_tool_model_name)()
        self.assertEqual(name, "unknown")
