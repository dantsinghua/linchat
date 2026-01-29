"""
模型配置序列化器单元测试 (T026 + T028)

覆盖:
- ModelResponseSerializer 输出字段验证
- ModelUpdateSerializer 校验规则
  - 必填字段空值拒绝
  - api_key 脱敏值跳过长度校验
  - api_key 新值最少 12 字符
  - temperature/top_p/frequency_penalty/presence_penalty 范围校验
  - embedding_dimensions 跨字段校验
  - NULL vs 0 正确接受
"""
from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from apps.models.serializers import ModelResponseSerializer, ModelUpdateSerializer


class TestModelResponseSerializer(TestCase):
    """ModelResponseSerializer 测试 (T026)"""

    def _make_data(self, **overrides):
        """构造响应数据"""
        data = {
            "id": 1,
            "type": "language",
            "name": "test-model",
            "url": "https://api.example.com/v1",
            "api_key": "test****5678",
            "max_context_window": 65536,
            "max_input_tokens": 32768,
            "max_output_tokens": 8192,
            "temperature": 0.7,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
            "embedding_dimensions": None,
            "is_active": True,
            "effective_context_window": 58982,
            "created_at": timezone.now(),
            "updated_at": timezone.now(),
        }
        data.update(overrides)
        return data

    def test_all_fields_present(self):
        """测试所有字段存在"""
        serializer = ModelResponseSerializer(data=self._make_data())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        expected_keys = {
            "id", "type", "name", "url", "api_key",
            "max_context_window", "max_input_tokens", "max_output_tokens",
            "temperature", "top_p", "frequency_penalty", "presence_penalty",
            "embedding_dimensions", "is_active", "effective_context_window",
            "created_at", "updated_at",
        }
        self.assertEqual(set(serializer.validated_data.keys()), expected_keys)

    def test_api_key_masked_output(self):
        """测试 api_key 脱敏输出"""
        data = self._make_data(api_key="test****5678")
        serializer = ModelResponseSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["api_key"], "test****5678")

    def test_null_optional_fields(self):
        """测试选填字段可以为 NULL"""
        data = self._make_data(
            temperature=None, top_p=None,
            frequency_penalty=None, presence_penalty=None,
        )
        serializer = ModelResponseSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertIsNone(serializer.validated_data["temperature"])

    def test_effective_context_window(self):
        """测试 effective_context_window 字段"""
        data = self._make_data(effective_context_window=58982)
        serializer = ModelResponseSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["effective_context_window"], 58982)


class TestModelUpdateSerializer(TestCase):
    """ModelUpdateSerializer 测试 (T028)"""

    def _make_data(self, **overrides):
        """构造更新请求数据"""
        data = {
            "name": "test-model",
            "url": "https://api.example.com/v1",
            "api_key": "test****5678",  # 脱敏值
            "max_context_window": 65536,
            "max_input_tokens": 32768,
            "max_output_tokens": 8192,
        }
        data.update(overrides)
        return data

    def _validate(self, data, model_type="language"):
        """执行校验"""
        serializer = ModelUpdateSerializer(
            data=data,
            context={"model_type": model_type},
        )
        return serializer

    # ===== 必填字段校验 =====

    def test_valid_basic_data(self):
        """测试基本有效数据"""
        s = self._validate(self._make_data())
        self.assertTrue(s.is_valid(), s.errors)

    def test_name_required(self):
        """测试 name 必填"""
        data = self._make_data()
        data.pop("name")
        s = self._validate(data)
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_empty_rejected(self):
        """测试 name 空字符串被拒绝"""
        s = self._validate(self._make_data(name=""))
        self.assertFalse(s.is_valid())

    def test_name_max_length(self):
        """测试 name 最大长度 100"""
        s = self._validate(self._make_data(name="a" * 101))
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_url_required(self):
        """测试 url 必填"""
        data = self._make_data()
        data.pop("url")
        s = self._validate(data)
        self.assertFalse(s.is_valid())

    def test_url_max_length(self):
        """测试 url 最大长度 500"""
        s = self._validate(self._make_data(url="a" * 501))
        self.assertFalse(s.is_valid())

    # ===== API Key 校验 =====

    def test_api_key_masked_passes(self):
        """测试脱敏值（含 ****）跳过长度校验"""
        s = self._validate(self._make_data(api_key="ab****cd"))
        self.assertTrue(s.is_valid(), s.errors)

    def test_api_key_masked_short_passes(self):
        """测试纯 **** 脱敏值通过"""
        s = self._validate(self._make_data(api_key="****"))
        self.assertTrue(s.is_valid(), s.errors)

    def test_api_key_new_value_min_12(self):
        """测试新 API Key 最少 12 字符"""
        s = self._validate(self._make_data(api_key="short-key"))
        self.assertFalse(s.is_valid())
        self.assertIn("api_key", s.errors)

    def test_api_key_new_value_valid(self):
        """测试新 API Key 12+ 字符通过"""
        s = self._validate(self._make_data(api_key="a-valid-long-api-key"))
        self.assertTrue(s.is_valid(), s.errors)

    # ===== 容量参数校验 =====

    def test_max_context_window_zero_rejected(self):
        """测试 max_context_window=0 被拒绝"""
        s = self._validate(self._make_data(max_context_window=0))
        self.assertFalse(s.is_valid())

    def test_max_context_window_negative_rejected(self):
        """测试 max_context_window 负值被拒绝"""
        s = self._validate(self._make_data(max_context_window=-1))
        self.assertFalse(s.is_valid())

    def test_max_input_tokens_zero_rejected(self):
        """测试 max_input_tokens=0 被拒绝"""
        s = self._validate(self._make_data(max_input_tokens=0))
        self.assertFalse(s.is_valid())

    # ===== 选填参数范围校验 =====

    def test_temperature_valid_range(self):
        """测试 temperature 有效范围 [0, 2]"""
        s = self._validate(self._make_data(temperature=0.5))
        self.assertTrue(s.is_valid(), s.errors)

    def test_temperature_zero_accepted(self):
        """测试 temperature=0 被接受"""
        s = self._validate(self._make_data(temperature=0))
        self.assertTrue(s.is_valid(), s.errors)

    def test_temperature_max_accepted(self):
        """测试 temperature=2 被接受"""
        s = self._validate(self._make_data(temperature=2))
        self.assertTrue(s.is_valid(), s.errors)

    def test_temperature_above_max_rejected(self):
        """测试 temperature > 2 被拒绝"""
        s = self._validate(self._make_data(temperature=2.1))
        self.assertFalse(s.is_valid())
        self.assertIn("temperature", s.errors)

    def test_temperature_below_min_rejected(self):
        """测试 temperature < 0 被拒绝"""
        s = self._validate(self._make_data(temperature=-0.1))
        self.assertFalse(s.is_valid())

    def test_temperature_null_accepted(self):
        """测试 temperature=null 被接受"""
        s = self._validate(self._make_data(temperature=None))
        self.assertTrue(s.is_valid(), s.errors)
        self.assertIsNone(s.validated_data.get("temperature"))

    def test_top_p_valid(self):
        """测试 top_p 有效值"""
        s = self._validate(self._make_data(top_p=0.9))
        self.assertTrue(s.is_valid(), s.errors)

    def test_top_p_above_max_rejected(self):
        """测试 top_p > 1 被拒绝"""
        s = self._validate(self._make_data(top_p=1.1))
        self.assertFalse(s.is_valid())

    def test_frequency_penalty_valid(self):
        """测试 frequency_penalty 有效范围 [-2, 2]"""
        s = self._validate(self._make_data(frequency_penalty=-1.5))
        self.assertTrue(s.is_valid(), s.errors)

    def test_frequency_penalty_out_of_range(self):
        """测试 frequency_penalty 超出范围"""
        s = self._validate(self._make_data(frequency_penalty=-2.1))
        self.assertFalse(s.is_valid())

    def test_presence_penalty_valid(self):
        """测试 presence_penalty 有效值"""
        s = self._validate(self._make_data(presence_penalty=1.0))
        self.assertTrue(s.is_valid(), s.errors)

    # ===== NULL vs 0 语义 =====

    def test_optional_fields_not_provided(self):
        """测试选填字段不提供"""
        data = self._make_data()
        # 不包含任何选填字段
        s = self._validate(data)
        self.assertTrue(s.is_valid(), s.errors)
        # 不在 validated_data 中
        self.assertNotIn("temperature", s.validated_data)

    def test_optional_field_zero_accepted(self):
        """测试选填字段为 0 被接受"""
        s = self._validate(self._make_data(
            temperature=0, top_p=0, frequency_penalty=0, presence_penalty=0
        ))
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["temperature"], 0)
        self.assertEqual(s.validated_data["top_p"], 0)

    # ===== 跨字段校验 =====

    def test_language_model_embedding_dimensions_null_accepted(self):
        """测试语言模型 embedding_dimensions=null 被接受"""
        s = self._validate(
            self._make_data(embedding_dimensions=None),
            model_type="language",
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_language_model_embedding_dimensions_rejected(self):
        """测试语言模型设置 embedding_dimensions 被拒绝"""
        s = self._validate(
            self._make_data(embedding_dimensions=1536),
            model_type="language",
        )
        self.assertFalse(s.is_valid())
        self.assertIn("embedding_dimensions", s.errors)

    def test_embedding_model_embedding_dimensions_accepted(self):
        """测试嵌入模型 embedding_dimensions 被接受"""
        s = self._validate(
            self._make_data(embedding_dimensions=1536),
            model_type="embedding",
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_embedding_model_embedding_dimensions_zero_rejected(self):
        """测试嵌入模型 embedding_dimensions=0 被拒绝"""
        s = self._validate(
            self._make_data(embedding_dimensions=0),
            model_type="embedding",
        )
        self.assertFalse(s.is_valid())
