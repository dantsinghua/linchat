"""
视图层 API 集成测试 [T028]

覆盖 5 个端点 (GET/POST list, GET/PUT/DELETE detail)，
认证校验、参数验证、响应格式
"""

from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.memory.models import UserMemory


def _authed_client(user_id: int = 1) -> APIClient:
    """创建模拟认证的 API 客户端（设置 cookie 并 mock 中间件验证）"""
    client = APIClient()
    client.cookies["linchat_token"] = "fake-token"
    return client


def _mock_verify(user_id: int = 1):
    """返回 mock _verify_token_sync 的 context manager"""
    return patch(
        "apps.common.middleware.TokenAuthMiddleware._verify_token_sync",
        return_value={
            "user_id": user_id,
            "username": f"user{user_id}",
            "user_type": "user",
        },
    )


class TestMemoryListCreate(TransactionTestCase):
    """GET/POST /api/v1/memories/ 测试"""

    @patch("apps.memory.tasks.generate_embedding")
    def test_create_success(self, mock_task: MagicMock) -> None:
        """POST 创建记忆 → 201"""
        mock_task.delay = MagicMock()
        client = _authed_client()

        with _mock_verify():
            response = client.post(
                "/api/v1/memories/",
                data={"content": "新记忆"},
                format="json",
            )

        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "SUCCESS"
        assert body["data"]["content"] == "新记忆"
        assert body["data"]["type"] == "memory"

    def test_create_empty_content(self) -> None:
        """POST 空内容 → 400 验证错误"""
        client = _authed_client()

        with _mock_verify():
            response = client.post(
                "/api/v1/memories/",
                data={"content": ""},
                format="json",
            )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"

    def test_create_missing_content(self) -> None:
        """POST 缺少 content → 400"""
        client = _authed_client()

        with _mock_verify():
            response = client.post(
                "/api/v1/memories/",
                data={},
                format="json",
            )

        assert response.status_code == 400

    @patch("apps.memory.tasks.generate_embedding")
    def test_list_success(self, mock_task: MagicMock) -> None:
        """GET 列表 → 200 分页响应"""
        mock_task.delay = MagicMock()
        for i in range(3):
            UserMemory.objects.create(user_id=1, content=f"item {i}")

        client = _authed_client()
        with _mock_verify():
            response = client.get("/api/v1/memories/")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "SUCCESS"
        assert body["data"]["total"] == 3
        assert len(body["data"]["items"]) == 3

    def test_list_with_type_filter(self) -> None:
        """GET 按类型过滤"""
        UserMemory.objects.create(user_id=1, content="a", type="memory")
        UserMemory.objects.create(user_id=1, content="b", type="compaction")

        client = _authed_client()
        with _mock_verify():
            response = client.get("/api/v1/memories/?type=memory")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 1

    def test_list_with_pagination(self) -> None:
        """GET 分页参数"""
        for i in range(5):
            UserMemory.objects.create(user_id=1, content=f"item {i}")

        client = _authed_client()
        with _mock_verify():
            response = client.get(
                "/api/v1/memories/?page=1&page_size=2"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 5
        assert len(body["data"]["items"]) == 2
        assert body["data"]["totalPages"] == 3

    def test_unauthenticated_request(self) -> None:
        """未认证请求 → 401"""
        client = APIClient()
        response = client.get("/api/v1/memories/")
        assert response.status_code == 401


class TestMemoryDetail(TransactionTestCase):
    """GET/PUT/DELETE /api/v1/memories/<id>/ 测试"""

    def test_get_success(self) -> None:
        """GET 获取详情 → 200"""
        memory = UserMemory.objects.create(user_id=1, content="详情测试")
        client = _authed_client()

        with _mock_verify():
            response = client.get(f"/api/v1/memories/{memory.id}/")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "SUCCESS"
        assert body["data"]["content"] == "详情测试"

    def test_get_not_found(self) -> None:
        """GET 不存在 → 404"""
        client = _authed_client()

        with _mock_verify():
            response = client.get("/api/v1/memories/99999/")

        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "NOT_FOUND"

    def test_get_wrong_user(self) -> None:
        """GET 他人记忆 → 404（用户隔离）"""
        memory = UserMemory.objects.create(user_id=999, content="other user")
        client = _authed_client()

        with _mock_verify(user_id=1):
            response = client.get(f"/api/v1/memories/{memory.id}/")

        assert response.status_code == 404

    @patch("apps.memory.tasks.generate_embedding")
    def test_update_success(self, mock_task: MagicMock) -> None:
        """PUT 更新记忆 → 200"""
        mock_task.delay = MagicMock()
        memory = UserMemory.objects.create(user_id=1, content="旧内容")
        client = _authed_client()

        with _mock_verify():
            response = client.put(
                f"/api/v1/memories/{memory.id}/",
                data={"content": "新内容"},
                format="json",
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["content"] == "新内容"

    def test_update_not_found(self) -> None:
        """PUT 不存在 → 404"""
        client = _authed_client()

        with _mock_verify():
            response = client.put(
                "/api/v1/memories/99999/",
                data={"content": "test"},
                format="json",
            )

        assert response.status_code == 404

    def test_update_empty_content(self) -> None:
        """PUT 空内容 → 400"""
        memory = UserMemory.objects.create(user_id=1, content="test")
        client = _authed_client()

        with _mock_verify():
            response = client.put(
                f"/api/v1/memories/{memory.id}/",
                data={"content": ""},
                format="json",
            )

        assert response.status_code == 400

    def test_delete_success(self) -> None:
        """DELETE 删除记忆 → 200"""
        memory = UserMemory.objects.create(user_id=1, content="待删除")
        client = _authed_client()

        with _mock_verify():
            response = client.delete(f"/api/v1/memories/{memory.id}/")

        assert response.status_code == 200
        assert UserMemory.objects.filter(id=memory.id).count() == 0

    def test_delete_not_found(self) -> None:
        """DELETE 不存在 → 404"""
        client = _authed_client()

        with _mock_verify():
            response = client.delete("/api/v1/memories/99999/")

        assert response.status_code == 404

    def test_delete_wrong_user(self) -> None:
        """DELETE 他人记忆 → 404"""
        memory = UserMemory.objects.create(user_id=999, content="other")
        client = _authed_client()

        with _mock_verify(user_id=1):
            response = client.delete(f"/api/v1/memories/{memory.id}/")

        assert response.status_code == 404


class TestMemoryResponseFormat(TransactionTestCase):
    """统一响应格式验证"""

    def test_response_has_code_message_data(self) -> None:
        """响应必须包含 code/message/data 字段"""
        UserMemory.objects.create(user_id=1, content="test")
        client = _authed_client()

        with _mock_verify():
            response = client.get("/api/v1/memories/")

        body = response.json()
        assert "code" in body
        assert "message" in body
        assert "data" in body

    def test_paginated_response_fields(self) -> None:
        """分页响应包含 items/total/page/pageSize/totalPages"""
        client = _authed_client()

        with _mock_verify():
            response = client.get("/api/v1/memories/")

        body = response.json()
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        assert "totalPages" in data


class TestMemorySearch(TransactionTestCase):
    """POST /api/v1/memories/search/ 测试 [T037]"""

    @patch("apps.memory.services.embedding_repo.keyword_search")
    @patch("apps.memory.services.embedding_repo.vector_search")
    @patch("apps.memory.services.EmbeddingClient.generate_embedding")
    def test_search_success(
        self, mock_embed, mock_vector, mock_keyword
    ) -> None:
        """正常搜索返回结果"""
        from unittest.mock import AsyncMock

        memory = UserMemory.objects.create(
            user_id=1, content="测试搜索", embedding_status="done"
        )

        mock_embed.return_value = [0.1] * 1024
        mock_vector.return_value = [(memory.id, 0.9)]
        mock_keyword.return_value = []

        client = _authed_client()
        with _mock_verify():
            response = client.post(
                "/api/v1/memories/search/",
                data={"query": "搜索"},
                format="json",
            )

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "SUCCESS"
        assert isinstance(body["data"], list)

    def test_search_empty_query(self) -> None:
        """空查询 → 400"""
        client = _authed_client()
        with _mock_verify():
            response = client.post(
                "/api/v1/memories/search/",
                data={"query": ""},
                format="json",
            )

        assert response.status_code == 400

    def test_search_missing_query(self) -> None:
        """缺少 query → 400"""
        client = _authed_client()
        with _mock_verify():
            response = client.post(
                "/api/v1/memories/search/",
                data={},
                format="json",
            )

        assert response.status_code == 400

    def test_search_limit_validation(self) -> None:
        """limit 超出范围 → 400"""
        client = _authed_client()
        with _mock_verify():
            response = client.post(
                "/api/v1/memories/search/",
                data={"query": "test", "limit": 100},
                format="json",
            )

        assert response.status_code == 400

    def test_search_unauthenticated(self) -> None:
        """未认证 → 401"""
        client = APIClient()
        response = client.post(
            "/api/v1/memories/search/",
            data={"query": "test"},
            format="json",
        )
        assert response.status_code == 401
