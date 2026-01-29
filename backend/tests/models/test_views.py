"""
模型配置视图单元测试 (T027 + T029)

覆盖:
- GET 列表接口返回 2 条记录
- GET 详情接口返回正确数据
- 统一响应格式 {code, message, data}
- 未认证用户返回 401
- 非管理员用户返回 403
- POST/DELETE/PATCH 返回 405（FR-013）
- PUT 正常更新
- PUT 校验失败 400
- PUT api_key **** 保留原值
- PUT api_key 新值加密存储
"""
import json

from django.test import TestCase, RequestFactory
from rest_framework.test import APIRequestFactory

from apps.models.models import ModelConfig
from apps.models.views import ModelDetailView, ModelListView
from apps.users.crypto import sm4_decrypt, sm4_encrypt


def _set_auth(request, user_type="admin"):
    """模拟认证中间件设置的属性"""
    request.user_id = 1
    request.username = "admin"
    request.user_type = user_type
    request.token_hash = "test_hash"


class TestModelListView(TestCase):
    """GET /api/v1/models/ 列表视图测试 (T027)"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.factory = APIRequestFactory()
        self.view = ModelListView.as_view()
        # 创建测试数据
        ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test-language",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("lang-key-123456789"),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
        )
        ModelConfig.objects.create(
            type=ModelConfig.TYPE_EMBEDDING,
            name="test-embedding",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt("embed-key-12345678"),
            max_context_window=8192,
            max_input_tokens=8192,
            max_output_tokens=1,
            embedding_dimensions=1536,
        )

    def test_get_list_success(self):
        """测试 GET 列表 - 成功返回 2 条记录"""
        request = self.factory.get("/api/v1/models/")
        _set_auth(request)
        response = self.view(request)
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["code"], "SUCCESS")
        self.assertEqual(len(data["data"]), 2)

    def test_get_list_response_format(self):
        """测试统一响应格式 {code, message, data}"""
        request = self.factory.get("/api/v1/models/")
        _set_auth(request)
        response = self.view(request)
        data = response.data
        self.assertIn("code", data)
        self.assertIn("message", data)
        self.assertIn("data", data)

    def test_get_list_api_key_masked(self):
        """测试列表中 API Key 脱敏"""
        request = self.factory.get("/api/v1/models/")
        _set_auth(request)
        response = self.view(request)
        for item in response.data["data"]:
            self.assertIn("****", item["api_key"])

    def test_unauthenticated_returns_403(self):
        """测试未认证用户返回 403（无 user_type 属性）"""
        request = self.factory.get("/api/v1/models/")
        # 不设置 auth 属性
        response = self.view(request)
        self.assertEqual(response.status_code, 403)

    def test_non_admin_returns_403(self):
        """测试非管理员返回 403"""
        request = self.factory.get("/api/v1/models/")
        _set_auth(request, user_type="user")
        response = self.view(request)
        self.assertEqual(response.status_code, 403)

    def test_post_returns_405(self):
        """测试 POST 返回 405（FR-013）"""
        request = self.factory.post("/api/v1/models/", {})
        _set_auth(request)
        response = self.view(request)
        self.assertEqual(response.status_code, 405)

    def test_delete_returns_405(self):
        """测试 DELETE 返回 405"""
        request = self.factory.delete("/api/v1/models/")
        _set_auth(request)
        response = self.view(request)
        self.assertEqual(response.status_code, 405)


class TestModelDetailView(TestCase):
    """GET/PUT /api/v1/models/<id>/ 详情视图测试 (T027 + T029)"""

    def setUp(self):
        ModelConfig.objects.all().delete()
        self.factory = APIRequestFactory()
        self.view = ModelDetailView.as_view()
        self.original_key = "original-api-key-123456"
        self.model = ModelConfig.objects.create(
            type=ModelConfig.TYPE_LANGUAGE,
            name="test-model",
            url="https://api.example.com/v1",
            api_key=sm4_encrypt(self.original_key),
            max_context_window=65536,
            max_input_tokens=32768,
            max_output_tokens=8192,
        )

    # ===== GET 详情 =====

    def test_get_detail_success(self):
        """测试 GET 详情 - 成功"""
        request = self.factory.get(f"/api/v1/models/{self.model.id}/")
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["data"]["name"], "test-model")

    def test_get_detail_not_found(self):
        """测试 GET 详情 - 不存在"""
        request = self.factory.get("/api/v1/models/99999/")
        _set_auth(request)
        response = self.view(request, pk=99999)
        self.assertEqual(response.status_code, 404)

    def test_get_detail_api_key_masked(self):
        """测试详情 API Key 脱敏"""
        request = self.factory.get(f"/api/v1/models/{self.model.id}/")
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertIn("****", response.data["data"]["api_key"])

    def test_get_detail_unauthenticated(self):
        """测试未认证访问详情"""
        request = self.factory.get(f"/api/v1/models/{self.model.id}/")
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 403)

    def test_get_detail_non_admin(self):
        """测试非管理员访问详情"""
        request = self.factory.get(f"/api/v1/models/{self.model.id}/")
        _set_auth(request, user_type="user")
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 403)

    # ===== PUT 更新 =====

    def test_put_success(self):
        """测试 PUT 更新 - 成功"""
        request = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "updated-model",
                "url": "https://new-api.example.com/v1",
                "api_key": "orig****3456",  # 脱敏值
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["name"], "updated-model")

    def test_put_validation_error(self):
        """测试 PUT 校验失败 - 400"""
        request = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "",  # 空名称
                "url": "https://api.example.com/v1",
                "api_key": "****",
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 400)

    def test_put_api_key_masked_keeps_original(self):
        """测试 PUT api_key **** 保留原值"""
        request = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "test-model",
                "url": "https://api.example.com/v1",
                "api_key": "orig****3456",
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request)
        self.view(request, pk=self.model.id)
        self.model.refresh_from_db()
        decrypted = sm4_decrypt(self.model.api_key)
        self.assertEqual(decrypted, self.original_key)

    def test_put_api_key_new_value_encrypted(self):
        """测试 PUT 新 api_key 加密存储"""
        new_key = "brand-new-api-key-xyz"
        request = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "test-model",
                "url": "https://api.example.com/v1",
                "api_key": new_key,
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request)
        self.view(request, pk=self.model.id)
        self.model.refresh_from_db()
        decrypted = sm4_decrypt(self.model.api_key)
        self.assertEqual(decrypted, new_key)

    def test_put_not_found(self):
        """测试 PUT 模型不存在"""
        request = self.factory.put(
            "/api/v1/models/99999/",
            data={
                "name": "test",
                "url": "https://api.example.com",
                "api_key": "****",
                "max_context_window": 1000,
                "max_input_tokens": 500,
                "max_output_tokens": 200,
            },
            format="json",
        )
        _set_auth(request)
        response = self.view(request, pk=99999)
        self.assertEqual(response.status_code, 404)

    def test_put_non_admin_returns_403(self):
        """测试非管理员 PUT 返回 403"""
        request = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={"name": "test"},
            format="json",
        )
        _set_auth(request, user_type="user")
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 403)

    # ===== 禁止的方法 =====

    def test_post_returns_405(self):
        """测试 POST 返回 405"""
        request = self.factory.post(
            f"/api/v1/models/{self.model.id}/", {}, format="json"
        )
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 405)

    def test_delete_returns_405(self):
        """测试 DELETE 返回 405"""
        request = self.factory.delete(f"/api/v1/models/{self.model.id}/")
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 405)

    def test_patch_returns_405(self):
        """测试 PATCH 返回 405（FR-013 禁止部分更新）"""
        request = self.factory.patch(
            f"/api/v1/models/{self.model.id}/",
            data={"name": "patched"},
            format="json",
        )
        _set_auth(request)
        response = self.view(request, pk=self.model.id)
        self.assertEqual(response.status_code, 405)

    # ===== 并发 PUT =====

    def test_concurrent_put_last_write_wins(self):
        """测试并发 PUT - 最后写入优先"""
        # 第一个 PUT
        request1 = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "first-update",
                "url": "https://api.example.com/v1",
                "api_key": "****",
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request1)
        self.view(request1, pk=self.model.id)

        # 第二个 PUT（最后写入）
        request2 = self.factory.put(
            f"/api/v1/models/{self.model.id}/",
            data={
                "name": "second-update",
                "url": "https://api.example.com/v1",
                "api_key": "****",
                "max_context_window": 65536,
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
            },
            format="json",
        )
        _set_auth(request2)
        self.view(request2, pk=self.model.id)

        # 验证最终值为第二次更新
        self.model.refresh_from_db()
        self.assertEqual(self.model.name, "second-update")
