"""
用户隔离专项测试 [T029]

验证 user_id 隔离在 Repository、Service、View 三层生效。
跨用户访问必须被拒绝 [R-004]。
"""

from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase
from rest_framework.test import APIClient

import pytest

from apps.memory.models import UserMemory, UserMemoryEmbedding
from apps.memory.repositories import embedding_repo, memory_repo
from apps.memory.services import MemoryNotFoundError, MemoryService
from tests.helpers import run_async


class TestRepositoryIsolation(TransactionTestCase):
    """Repository 层用户隔离"""

    def test_get_by_id_cross_user(self) -> None:
        """user_id 不匹配时 get_by_id 返回 None"""
        memory = UserMemory.objects.create(user_id=1, content="user1 data")

        result = run_async(memory_repo.get_by_id(memory.id, user_id=2))
        assert result is None

    def test_delete_cross_user(self) -> None:
        """user_id 不匹配时 delete 返回 False"""
        memory = UserMemory.objects.create(user_id=1, content="user1 data")

        deleted = run_async(memory_repo.delete(memory.id, user_id=2))
        assert deleted is False

        # 原记录仍存在
        assert UserMemory.objects.filter(id=memory.id).exists()

    def test_list_by_user_only_own(self) -> None:
        """list_by_user 只返回自己的记忆"""
        UserMemory.objects.create(user_id=1, content="user1")
        UserMemory.objects.create(user_id=2, content="user2")
        UserMemory.objects.create(user_id=1, content="user1 again")

        memories, total = run_async(memory_repo.list_by_user(user_id=1))
        assert total == 2
        for m in memories:
            assert m.user_id == 1

    def test_embedding_get_cross_user(self) -> None:
        """Embedding 查询也隔离 user_id"""
        memory = UserMemory.objects.create(user_id=1, content="test")
        UserMemoryEmbedding.objects.create(
            memory=memory, user_id=1, type="memory", chunk_text="test"
        )

        # 用户 2 查询不到用户 1 的 embedding
        result = run_async(embedding_repo.get_by_memory_id(memory.id, user_id=2))
        assert len(result) == 0


class TestServiceIsolation(TransactionTestCase):
    """Service 层用户隔离"""

    def test_get_memory_cross_user(self) -> None:
        """get_memory 跨用户 → MemoryNotFoundError"""
        memory = UserMemory.objects.create(user_id=1, content="user1")

        with pytest.raises(MemoryNotFoundError):
            run_async(MemoryService.get_memory(memory_id=memory.id, user_id=2))

    def test_update_memory_cross_user(self) -> None:
        """update_memory 跨用户 → MemoryNotFoundError"""
        memory = UserMemory.objects.create(user_id=1, content="user1")

        with pytest.raises(MemoryNotFoundError):
            run_async(
                MemoryService.update_memory(
                    memory_id=memory.id, user_id=2, content="hacked"
                )
            )

        # 原内容不变
        memory.refresh_from_db()
        assert memory.content == "user1"

    def test_delete_memory_cross_user(self) -> None:
        """delete_memory 跨用户 → MemoryNotFoundError"""
        memory = UserMemory.objects.create(user_id=1, content="user1")

        with pytest.raises(MemoryNotFoundError):
            run_async(
                MemoryService.delete_memory(memory_id=memory.id, user_id=2)
            )

        # 记录仍存在
        assert UserMemory.objects.filter(id=memory.id).exists()

    def test_list_memories_cross_user(self) -> None:
        """list_memories 只列出自己的记忆"""
        UserMemory.objects.create(user_id=1, content="user1 a")
        UserMemory.objects.create(user_id=1, content="user1 b")
        UserMemory.objects.create(user_id=2, content="user2")

        memories, total = run_async(MemoryService.list_memories(user_id=1))
        assert total == 2

        memories2, total2 = run_async(MemoryService.list_memories(user_id=2))
        assert total2 == 1


class TestViewIsolation(TransactionTestCase):
    """View 层用户隔离（端到端）"""

    def _request_as_user(self, user_id: int, method: str, url: str, data=None):
        """模拟指定用户发送请求"""
        client = APIClient()
        client.cookies["linchat_token"] = "fake-token"
        with patch(
            "apps.common.middleware.TokenAuthMiddleware._verify_token_sync",
            return_value={
                "user_id": user_id,
                "username": f"user{user_id}",
                "user_type": "user",
            },
        ):
            if method == "GET":
                return client.get(url)
            if method == "DELETE":
                return client.delete(url)
            return client.put(url, data=data, format="json")

    def test_get_detail_cross_user(self) -> None:
        """用户 2 无法 GET 用户 1 的记忆"""
        memory = UserMemory.objects.create(user_id=1, content="private")

        response = self._request_as_user(
            2, "GET", f"/api/v1/memories/{memory.id}/"
        )
        assert response.status_code == 404

    def test_update_cross_user(self) -> None:
        """用户 2 无法 PUT 用户 1 的记忆"""
        memory = UserMemory.objects.create(user_id=1, content="private")

        response = self._request_as_user(
            2,
            "PUT",
            f"/api/v1/memories/{memory.id}/",
            data={"content": "hacked"},
        )
        assert response.status_code == 404

        # 内容不变
        memory.refresh_from_db()
        assert memory.content == "private"

    def test_delete_cross_user(self) -> None:
        """用户 2 无法 DELETE 用户 1 的记忆"""
        memory = UserMemory.objects.create(user_id=1, content="private")

        response = self._request_as_user(
            2, "DELETE", f"/api/v1/memories/{memory.id}/"
        )
        assert response.status_code == 404

        # 记录仍存在
        assert UserMemory.objects.filter(id=memory.id).exists()

    def test_list_only_own_memories(self) -> None:
        """列表接口只返回自己的记忆"""
        UserMemory.objects.create(user_id=1, content="user1")
        UserMemory.objects.create(user_id=2, content="user2")

        response = self._request_as_user(1, "GET", "/api/v1/memories/")
        body = response.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["content"] == "user1"

        response2 = self._request_as_user(2, "GET", "/api/v1/memories/")
        body2 = response2.json()
        assert body2["data"]["total"] == 1
        assert body2["data"]["items"][0]["content"] == "user2"
