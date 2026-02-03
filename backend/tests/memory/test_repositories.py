"""仓库层测试 [T025] — MemoryRepository CRUD、用户隔离、EmbeddingRepository 基本读写"""

from datetime import timedelta

import pytest
from django.test import TransactionTestCase
from django.utils import timezone

from apps.memory.models import UserMemory, UserMemoryEmbedding
from apps.memory.repositories import embedding_repo, memory_repo
from tests.helpers import run_async


class TestMemoryRepository(TransactionTestCase):

    def test_create_and_get_by_id(self) -> None:
        memory = UserMemory(user_id=1, content="测试内容")
        result = run_async(memory_repo.create(memory))
        assert result.id is not None

        fetched = run_async(memory_repo.get_by_id(result.id, user_id=1))
        assert fetched is not None
        assert fetched.content == "测试内容"

    def test_get_by_id_wrong_user(self) -> None:
        memory = UserMemory(user_id=1, content="test")
        result = run_async(memory_repo.create(memory))
        fetched = run_async(memory_repo.get_by_id(result.id, user_id=999))
        assert fetched is None

    def test_user_id_none_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="user_id"):
            run_async(memory_repo.get_by_id(1, user_id=None))

    def test_delete(self) -> None:
        memory = run_async(memory_repo.create(UserMemory(user_id=1, content="to delete")))
        assert run_async(memory_repo.delete(memory.id, user_id=1)) is True
        assert run_async(memory_repo.get_by_id(memory.id, user_id=1)) is None

    def test_delete_wrong_user(self) -> None:
        memory = run_async(memory_repo.create(UserMemory(user_id=1, content="test")))
        assert run_async(memory_repo.delete(memory.id, user_id=999)) is False

    def test_list_by_user_with_pagination(self) -> None:
        for i in range(5):
            run_async(memory_repo.create(UserMemory(user_id=1, content=f"item {i}")))
        run_async(memory_repo.create(UserMemory(user_id=2, content="other user")))

        memories, total = run_async(memory_repo.list_by_user(user_id=1, page=1, page_size=3))
        assert total == 5
        assert len(memories) == 3

    def test_list_by_user_type_filter(self) -> None:
        run_async(memory_repo.create(UserMemory(user_id=1, content="a", type="memory")))
        run_async(memory_repo.create(UserMemory(user_id=1, content="b", type="compaction")))

        memories, total = run_async(memory_repo.list_by_user(user_id=1, type_filter="compaction"))
        assert total == 1
        assert memories[0].type == "compaction"

    def test_find_retryable(self) -> None:
        m1 = run_async(memory_repo.create(
            UserMemory(user_id=1, content="a", embedding_status="failed", retry_count=1)
        ))
        run_async(memory_repo.create(
            UserMemory(user_id=1, content="b", embedding_status="failed", retry_count=3)
        ))

        retryable = run_async(memory_repo.find_retryable(max_retry=3))
        assert len(retryable) == 1
        assert retryable[0].id == m1.id

    def test_find_pending_timeout(self) -> None:
        m = UserMemory.objects.create(user_id=1, content="test", embedding_status="pending")
        UserMemory.objects.filter(id=m.id).update(updated_at=timezone.now() - timedelta(seconds=600))

        timed_out = run_async(memory_repo.find_pending_timeout(timeout_seconds=300))
        assert len(timed_out) == 1

    def test_batch_get_by_ids(self) -> None:
        m1 = run_async(memory_repo.create(UserMemory(user_id=1, content="a")))
        m2 = run_async(memory_repo.create(UserMemory(user_id=1, content="b")))
        run_async(memory_repo.create(UserMemory(user_id=2, content="other")))

        result = run_async(memory_repo.batch_get_by_ids([m1.id, m2.id], user_id=1))
        assert len(result) == 2
        assert m1.id in result and m2.id in result

    def test_batch_get_by_ids_cross_user(self) -> None:
        m = run_async(memory_repo.create(UserMemory(user_id=1, content="a")))
        assert len(run_async(memory_repo.batch_get_by_ids([m.id], user_id=999))) == 0


class TestEmbeddingRepository(TransactionTestCase):

    def test_create_and_get(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        emb = UserMemoryEmbedding(memory=memory, user_id=1, type="memory", chunk_text="test")
        result = run_async(embedding_repo.create(emb))
        assert result.id is not None

        fetched = run_async(embedding_repo.get_by_memory_id(memory.id, user_id=1))
        assert len(fetched) == 1

    def test_delete_by_memory_id(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        UserMemoryEmbedding.objects.create(memory=memory, user_id=1, type="memory")
        assert run_async(embedding_repo.delete_by_memory_id(memory.id)) == 1

    def test_vector_search_skeleton_returns_empty(self) -> None:
        result = run_async(embedding_repo.vector_search(user_id=1, query_embedding=[0.0] * 1024))
        assert result == []

    def test_keyword_search_skeleton_returns_empty(self) -> None:
        result = run_async(embedding_repo.keyword_search(user_id=1, query_text="test"))
        assert result == []

    def test_user_id_none_raises(self) -> None:
        with pytest.raises(ValueError):
            run_async(embedding_repo.get_by_memory_id(1, user_id=None))
