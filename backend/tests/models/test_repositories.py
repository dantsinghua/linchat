"""
模型配置 Repository 层单元测试 (T024)

覆盖:
- get_all_models() 获取全部
- get_model_by_id() 正常和异常路径
- get_active_model_by_type() 正常和异常路径
- update_model() 字段更新
"""
from django.test import TestCase

from apps.models.models import ModelConfig
from apps.models.repositories import model_repo
from apps.users.crypto import sm4_encrypt


class TestModelRepository(TestCase):
    """ModelRepository 测试"""

    def setUp(self):
        """创建测试数据"""
        ModelConfig.objects.all().delete()
        self.tool_model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_TOOL,
            name="test-tool",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("test-key-123456789"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
            is_active=True,
        )
        self.embedding_model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_EMBEDDING,
            name="test-embedding",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("embed-key-123456"),
            max_context_window=8192,
            max_input_tokens=8192,
            max_output_tokens=1,
            embedding_dimensions=1536,
            is_active=True,
        )

    def test_get_all(self):
        """测试获取所有模型配置"""
        models = model_repo.get_all()
        self.assertEqual(len(models), 2)

    def test_get_all_order_by_id(self):
        """测试获取全部按 ID 排序"""
        models = model_repo.get_all()
        self.assertTrue(models[0].id < models[1].id)

    def test_get_by_id_exists(self):
        """测试根据 ID 获取 - 存在"""
        model = model_repo.get_by_id(self.tool_model.id)
        self.assertIsNotNone(model)
        self.assertEqual(model.name, "test-tool")

    def test_get_by_id_not_exists(self):
        """测试根据 ID 获取 - 不存在"""
        model = model_repo.get_by_id(99999)
        self.assertIsNone(model)

    def test_get_active_by_type_tool(self):
        """测试根据类型获取激活模型 - tool"""
        model = model_repo.get_active_by_type("tool")
        self.assertIsNotNone(model)
        self.assertEqual(model.type, "tool")
        self.assertEqual(model.name, "test-tool")

    def test_get_active_by_type_embedding(self):
        """测试根据类型获取激活模型 - embedding"""
        model = model_repo.get_active_by_type("embedding")
        self.assertIsNotNone(model)
        self.assertEqual(model.type, "embedding")

    def test_get_active_by_type_not_found(self):
        """测试根据类型获取 - 无激活模型"""
        self.tool_model.is_active = False
        self.tool_model.save()
        model = model_repo.get_active_by_type("tool")
        self.assertIsNone(model)

    def test_get_active_by_type_invalid(self):
        """测试根据类型获取 - 不存在的类型"""
        model = model_repo.get_active_by_type("nonexistent")
        self.assertIsNone(model)

    def test_update_single_field(self):
        """测试更新单个字段"""
        updated = model_repo.update(self.tool_model, name="updated-name")
        self.assertEqual(updated.name, "updated-name")
        # 从数据库重新加载验证
        model = model_repo.get_by_id(self.tool_model.id)
        self.assertEqual(model.name, "updated-name")

    def test_update_multiple_fields(self):
        """测试更新多个字段"""
        updated = model_repo.update(
            self.tool_model,
            name="new-name",
            url="https://new-api.example.com/v1",
            temperature=0.7,
        )
        self.assertEqual(updated.name, "new-name")
        self.assertEqual(updated.url, "https://new-api.example.com/v1")
        self.assertEqual(updated.temperature, 0.7)

    def test_update_optional_to_null(self):
        """测试将选填字段更新为 NULL"""
        # 先设为非 NULL
        model_repo.update(self.tool_model, temperature=0.5)
        self.assertEqual(
            model_repo.get_by_id(self.tool_model.id).temperature, 0.5
        )
        # 再设为 NULL
        model_repo.update(self.tool_model, temperature=None)
        self.assertIsNone(
            model_repo.get_by_id(self.tool_model.id).temperature
        )

    def test_update_optional_to_zero(self):
        """测试将选填字段更新为 0（NULL vs 0 语义）"""
        model_repo.update(self.tool_model, temperature=0)
        model = model_repo.get_by_id(self.tool_model.id)
        self.assertEqual(model.temperature, 0)
        self.assertIsNotNone(model.temperature)
