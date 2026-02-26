"""
模型配置数据模型单元测试 (T023)

覆盖:
- 字段约束验证
- 计算属性 effective_context_window
- 计算属性 masked_api_key
- choices 验证
- 表名验证
"""
import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.models.models import ModelConfig
from apps.users.crypto import sm4_encrypt


class TestModelConfigFields(TestCase):
    """ModelConfig 字段约束测试"""

    def _create_model(self, **kwargs) -> ModelConfig:
        """创建测试模型实例"""
        defaults = {
            "type": ModelConfig.TYPE_TOOL,
            "name": "test-model",
            "url": "https://api.example.com/v1",
            "api_key": sm4_encrypt("test-api-key-12345"),
            "max_context_window": 65536,
            "max_input_tokens": 32768,
            "max_output_tokens": 8192,
        }
        defaults.update(kwargs)
        return ModelConfig.objects.create(**defaults)

    def test_create_tool_model(self):
        """测试创建工具模型"""
        model = self._create_model(type=ModelConfig.TYPE_TOOL)
        self.assertEqual(model.type, "tool")
        self.assertEqual(model.name, "test-model")
        self.assertTrue(model.is_active)
        self.assertIsNotNone(model.created_at)
        self.assertIsNotNone(model.updated_at)

    def test_create_embedding_model(self):
        """测试创建嵌入模型"""
        model = self._create_model(
            type=ModelConfig.TYPE_EMBEDDING,
            name="embedding-model",
            embedding_dimensions=1536,
        )
        self.assertEqual(model.type, "embedding")
        self.assertEqual(model.embedding_dimensions, 1536)

    def test_type_choices(self):
        """测试类型选项"""
        self.assertEqual(ModelConfig.TYPE_TOOL, "tool")
        self.assertEqual(ModelConfig.TYPE_MULTIMODAL, "multimodal")
        self.assertEqual(ModelConfig.TYPE_EMBEDDING, "embedding")
        choices = dict(ModelConfig.TYPE_CHOICES)
        self.assertIn("tool", choices)
        self.assertIn("multimodal", choices)
        self.assertIn("embedding", choices)

    def test_optional_fields_null(self):
        """测试选填字段可以为 NULL"""
        model = self._create_model(
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            embedding_dimensions=None,
        )
        self.assertIsNone(model.temperature)
        self.assertIsNone(model.top_p)
        self.assertIsNone(model.frequency_penalty)
        self.assertIsNone(model.presence_penalty)
        self.assertIsNone(model.embedding_dimensions)

    def test_optional_fields_zero(self):
        """测试选填字段可以为 0（NULL vs 0 语义区分）"""
        model = self._create_model(
            temperature=0,
            top_p=0,
            frequency_penalty=0,
            presence_penalty=0,
        )
        self.assertEqual(model.temperature, 0)
        self.assertEqual(model.top_p, 0)
        self.assertEqual(model.frequency_penalty, 0)
        self.assertEqual(model.presence_penalty, 0)

    def test_max_context_window_validator(self):
        """测试 max_context_window 不允许 0"""
        model = ModelConfig(
            type=ModelConfig.TYPE_TOOL,
            name="test",
            url="https://api.example.com",
            api_key="encrypted",
            max_context_window=0,
            max_input_tokens=1000,
            max_output_tokens=1000,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_max_input_tokens_validator(self):
        """测试 max_input_tokens 不允许 0"""
        model = ModelConfig(
            type=ModelConfig.TYPE_TOOL,
            name="test",
            url="https://api.example.com",
            api_key="encrypted",
            max_context_window=1000,
            max_input_tokens=0,
            max_output_tokens=1000,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_temperature_out_of_range(self):
        """测试 temperature 超出范围 [0, 2]"""
        model = ModelConfig(
            type=ModelConfig.TYPE_TOOL,
            name="test",
            url="https://api.example.com",
            api_key="encrypted",
            max_context_window=1000,
            max_input_tokens=1000,
            max_output_tokens=1000,
            temperature=3.0,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_top_p_out_of_range(self):
        """测试 top_p 超出范围 [0, 1]"""
        model = ModelConfig(
            type=ModelConfig.TYPE_TOOL,
            name="test",
            url="https://api.example.com",
            api_key="encrypted",
            max_context_window=1000,
            max_input_tokens=1000,
            max_output_tokens=1000,
            top_p=1.5,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_frequency_penalty_out_of_range(self):
        """测试 frequency_penalty 超出范围 [-2, 2]"""
        model = ModelConfig(
            type=ModelConfig.TYPE_TOOL,
            name="test",
            url="https://api.example.com",
            api_key="encrypted",
            max_context_window=1000,
            max_input_tokens=1000,
            max_output_tokens=1000,
            frequency_penalty=-3.0,
        )
        with self.assertRaises(ValidationError):
            model.full_clean()

    def test_db_table_name(self):
        """测试数据表名"""
        self.assertEqual(ModelConfig._meta.db_table, "model")

    def test_str_representation(self):
        """测试字符串表示"""
        model = self._create_model()
        expected = f"ModelConfig({model.id}, tool, test-model)"
        self.assertEqual(str(model), expected)


class TestModelConfigComputedProperties(TestCase):
    """ModelConfig 计算属性测试"""

    def _create_model(self, **kwargs) -> ModelConfig:
        defaults = {
            "type": ModelConfig.TYPE_TOOL,
            "name": "test-model",
            "url": "https://api.example.com/v1",
            "api_key": sm4_encrypt("test-api-key-12345"),
            "max_context_window": 65536,
            "max_input_tokens": 32768,
            "max_output_tokens": 8192,
        }
        defaults.update(kwargs)
        return ModelConfig.objects.create(**defaults)

    def test_effective_context_window(self):
        """测试 effective_context_window = int(max_context_window * 0.9)"""
        model = self._create_model(max_context_window=65536)
        self.assertEqual(model.effective_context_window, int(65536 * 0.9))
        self.assertEqual(model.effective_context_window, 58982)

    def test_effective_context_window_small(self):
        """测试小值的 effective_context_window"""
        model = self._create_model(max_context_window=100)
        self.assertEqual(model.effective_context_window, 90)

    def test_effective_context_window_is_int(self):
        """测试 effective_context_window 为整数"""
        model = self._create_model(max_context_window=10)
        self.assertIsInstance(model.effective_context_window, int)

    def test_masked_api_key_long(self):
        """测试 masked_api_key：长密钥（> 8 字符）→ 前4+****+后4"""
        api_key_plain = "abcdefghij1234"  # 14 字符
        model = self._create_model(api_key=sm4_encrypt(api_key_plain))
        self.assertEqual(model.masked_api_key, "abcd****1234")

    def test_masked_api_key_short(self):
        """测试 masked_api_key：短密钥（<= 8 字符）→ ****"""
        api_key_plain = "12345678"  # 恰好 8 字符
        model = self._create_model(api_key=sm4_encrypt(api_key_plain))
        self.assertEqual(model.masked_api_key, "****")

    def test_masked_api_key_very_short(self):
        """测试 masked_api_key：极短密钥"""
        api_key_plain = "abc"
        model = self._create_model(api_key=sm4_encrypt(api_key_plain))
        self.assertEqual(model.masked_api_key, "****")
