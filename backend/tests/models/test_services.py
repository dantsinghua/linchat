"""
模型配置 Service 层单元测试 (T025)

覆盖:
- SM4 加解密流程
- API Key 脱敏逻辑（长密钥/短密钥，FR-009）
- **** 判断保留原值（FR-005）
- NULL vs 0 语义传递（FR-006, FR-007）
- get_active_model() 获取激活模型（FR-014）

覆盖率目标: ≥ 95%
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.models.models import ModelConfig
from apps.models.services import ModelService, _mask_api_key, _model_to_dict
from apps.users.crypto import sm4_decrypt, sm4_encrypt


class TestMaskApiKey(TestCase):
    """API Key 脱敏函数测试"""

    def test_long_key_masked(self):
        """长密钥（> 8）：前4 + **** + 后4"""
        self.assertEqual(_mask_api_key("abcdefghijklmnop"), "abcd****mnop")

    def test_exactly_nine_chars(self):
        """恰好 9 字符：前4 + **** + 后4"""
        self.assertEqual(_mask_api_key("123456789"), "1234****6789")

    def test_short_key_fully_masked(self):
        """短密钥（<= 8）：全部脱敏为 ****"""
        self.assertEqual(_mask_api_key("12345678"), "****")

    def test_very_short_key(self):
        """极短密钥"""
        self.assertEqual(_mask_api_key("abc"), "****")

    def test_empty_key(self):
        """空密钥"""
        self.assertEqual(_mask_api_key(""), "****")

    def test_none_key(self):
        """None 密钥"""
        self.assertEqual(_mask_api_key(None), "****")


class TestModelServiceGetAll(TestCase):
    """ModelService.get_all_models() 测试"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.service = ModelService()
        self.language_model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test-language",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("test-api-key-123456"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
        )

    def test_get_all_returns_list(self):
        """测试返回列表"""
        result = self.service.get_all_models()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)

    def test_get_all_api_key_masked(self):
        """测试 API Key 已脱敏"""
        result = self.service.get_all_models()
        api_key = result[0]["api_key"]
        self.assertIn("****", api_key)
        # 不应该包含完整明文
        self.assertNotEqual(api_key, "test-api-key-123456")

    def test_get_all_contains_computed_fields(self):
        """测试包含计算属性"""
        result = self.service.get_all_models()
        self.assertIn("effective_context_window", result[0])
        self.assertEqual(result[0]["effective_context_window"], int(65536 * 0.9))

    def test_get_all_all_fields_present(self):
        """测试所有字段存在"""
        result = self.service.get_all_models()
        expected_keys = {
            "id", "type", "name", "url", "api_key",
            "max_context_window", "max_input_tokens", "max_output_tokens",
            "temperature", "top_p", "frequency_penalty", "presence_penalty",
            "embedding_dimensions", "is_active", "effective_context_window",
            "created_at", "updated_at",
        }
        self.assertEqual(set(result[0].keys()), expected_keys)


class TestModelServiceGetById(TestCase):
    """ModelService.get_model_by_id() 测试"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.service = ModelService()
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test-model",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("long-api-key-for-testing"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
        )

    def test_get_by_id_exists(self):
        """测试获取存在的模型"""
        result = self.service.get_model_by_id(self.model.id)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "test-model")

    def test_get_by_id_not_exists(self):
        """测试获取不存在的模型"""
        result = self.service.get_model_by_id(99999)
        self.assertIsNone(result)

    def test_get_by_id_api_key_masked(self):
        """测试 API Key 脱敏"""
        result = self.service.get_model_by_id(self.model.id)
        self.assertIn("****", result["api_key"])


class TestModelServiceUpdate(TestCase):
    """ModelService.update_model() 测试"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.service = ModelService()
        self.original_key = "original-api-key-12345"
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test-model",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt(self.original_key),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
        )

    def test_update_basic_fields(self):
        """测试更新基本字段"""
        result = self.service.update_model(self.model.id, {
            "name": "updated-name",
            "url": "https://new-api.example.com/v1",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "updated-name")
        self.assertEqual(result["url"], "https://new-api.example.com/v1")

    def test_update_api_key_masked_keeps_original(self):
        """测试 api_key 包含 **** 时保留原值（FR-005）"""
        result = self.service.update_model(self.model.id, {
            "api_key": "orig****5678",
        })
        # 验证原始密钥未被改变
        self.model.refresh_from_db()
        decrypted = sm4_decrypt(self.model.api_key)
        self.assertEqual(decrypted, self.original_key)

    def test_update_api_key_new_value_encrypted(self):
        """测试新 api_key 被加密存储（FR-008）"""
        new_key = "brand-new-api-key-99999"
        result = self.service.update_model(self.model.id, {
            "api_key": new_key,
        })
        # 验证新密钥已加密存储
        self.model.refresh_from_db()
        decrypted = sm4_decrypt(self.model.api_key)
        self.assertEqual(decrypted, new_key)

    def test_update_not_found(self):
        """测试更新不存在的模型"""
        result = self.service.update_model(99999, {"name": "x"})
        self.assertIsNone(result)

    def test_update_type_is_ignored(self):
        """测试 type 字段被忽略（不可修改）"""
        self.service.update_model(self.model.id, {
            "type": "embedding",
        })
        self.model.refresh_from_db()
        self.assertEqual(self.model.type, "language")

    def test_update_is_active_is_ignored(self):
        """测试 is_active 字段被忽略"""
        self.service.update_model(self.model.id, {
            "is_active": False,
        })
        self.model.refresh_from_db()
        self.assertTrue(self.model.is_active)

    def test_update_returns_masked_key(self):
        """测试更新后返回脱敏 API Key"""
        result = self.service.update_model(self.model.id, {
            "name": "updated",
        })
        self.assertIn("****", result["api_key"])


class TestModelServiceNullVsZero(TestCase):
    """NULL vs 0 语义传递测试（FR-006, FR-007）"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.service = ModelService()
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("test-key-12345678"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
            temperature=None,
        )

    def test_temperature_null_stored_as_null(self):
        """测试 temperature=NULL 存储为 NULL"""
        result = self.service.get_model_by_id(self.model.id)
        self.assertIsNone(result["temperature"])

    def test_temperature_zero_stored_as_zero(self):
        """测试 temperature=0 存储为 0（非 NULL）"""
        self.service.update_model(self.model.id, {"temperature": 0})
        result = self.service.get_model_by_id(self.model.id)
        self.assertEqual(result["temperature"], 0)
        self.assertIsNotNone(result["temperature"])

    def test_clear_temperature_to_null(self):
        """测试清空 temperature（设为 NULL）"""
        # 先设为 0
        self.service.update_model(self.model.id, {"temperature": 0})
        # 再清空为 NULL
        self.service.update_model(self.model.id, {"temperature": None})
        result = self.service.get_model_by_id(self.model.id)
        self.assertIsNone(result["temperature"])


class TestModelServiceGetActiveModel(TestCase):
    """ModelService.get_active_model() 测试（FR-014）"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.service = ModelService()
        self.api_key_plain = "test-active-key-12345"
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="active-language",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt(self.api_key_plain),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
            temperature=0.7,
            is_active=True,
        )

    def test_get_active_language_model(self):
        """测试获取激活的语言模型"""
        result = self.service.get_active_model("language")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "active-language")
        self.assertEqual(result["type"], "language")

    def test_get_active_model_api_key_decrypted(self):
        """测试 API Key 为解密明文"""
        result = self.service.get_active_model("language")
        self.assertEqual(result["api_key"], self.api_key_plain)

    def test_get_active_model_not_found(self):
        """测试无激活模型"""
        result = self.service.get_active_model("embedding")
        self.assertIsNone(result)

    def test_get_active_model_inactive_not_returned(self):
        """测试未激活的模型不返回"""
        self.model.is_active = False
        self.model.save()
        result = self.service.get_active_model("language")
        self.assertIsNone(result)

    def test_get_active_model_includes_optional_params(self):
        """测试包含选填参数"""
        result = self.service.get_active_model("language")
        self.assertEqual(result["temperature"], 0.7)
        self.assertIsNone(result["top_p"])
