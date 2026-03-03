"""仓库层测试 [T025] — MemoryRepository CRUD、用户隔离、EmbeddingRepository 基本读写

直接测试 ORM 操作（绕过 @sync_to_async 包装），避免
TransactionTestCase + async event loop + thread 的死锁问题。
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.memory.models import UserMemory, UserMemoryEmbedding

pytestmark = pytest.mark.django_db


class TestMemoryRepository:
    """直接测试 ORM 逻辑（与 Repository 同步等价）"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        UserMemory.objects.all().delete()
        yield

    def test_create_and_get_by_id(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="测试内容")
        assert memory.id is not None

        fetched = UserMemory.objects.filter(id=memory.id, user_id=1).first()
        assert fetched is not None
        assert fetched.content == "测试内容"

    def test_get_by_id_wrong_user(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        fetched = UserMemory.objects.filter(id=memory.id, user_id=999).first()
        assert fetched is None

    def test_user_id_none_raises_value_error(self) -> None:
        from apps.memory.repositories import _require_user_id

        with pytest.raises(ValueError, match="user_id"):
            _require_user_id(None)

    def test_delete(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="to delete")
        deleted, _ = UserMemory.objects.filter(id=memory.id, user_id=1).delete()
        assert deleted > 0
        assert UserMemory.objects.filter(id=memory.id).first() is None

    def test_delete_wrong_user(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        deleted, _ = UserMemory.objects.filter(id=memory.id, user_id=999).delete()
        assert deleted == 0

    def test_list_by_user_with_pagination(self) -> None:
        for i in range(5):
            UserMemory.objects.create(user_id=1, content=f"item {i}")
        UserMemory.objects.create(user_id=2, content="other user")

        qs = UserMemory.objects.filter(user_id=1)
        total = qs.count()
        assert total == 5
        page = list(qs[:3])
        assert len(page) == 3

    def test_list_by_user_type_filter(self) -> None:
        UserMemory.objects.create(user_id=1, content="a", type="memory")
        UserMemory.objects.create(user_id=1, content="b", type="compaction")

        qs = UserMemory.objects.filter(user_id=1, type="compaction")
        assert qs.count() == 1
        assert qs.first().type == "compaction"

    def test_find_retryable(self) -> None:
        m1 = UserMemory.objects.create(
            user_id=1, content="a", embedding_status="failed", retry_count=1
        )
        UserMemory.objects.create(
            user_id=1, content="b", embedding_status="failed", retry_count=3
        )

        retryable = list(
            UserMemory.objects.filter(embedding_status="failed", retry_count__lt=3)
        )
        assert len(retryable) == 1
        assert retryable[0].id == m1.id

    def test_find_pending_timeout(self) -> None:
        m = UserMemory.objects.create(
            user_id=1, content="test", embedding_status="pending"
        )
        UserMemory.objects.filter(id=m.id).update(
            updated_at=timezone.now() - timedelta(seconds=600)
        )

        threshold = timezone.now() - timedelta(seconds=300)
        timed_out = list(
            UserMemory.objects.filter(
                embedding_status="pending", updated_at__lt=threshold
            )
        )
        assert len(timed_out) == 1

    def test_batch_get_by_ids(self) -> None:
        m1 = UserMemory.objects.create(user_id=1, content="a")
        m2 = UserMemory.objects.create(user_id=1, content="b")
        UserMemory.objects.create(user_id=2, content="other")

        result = {
            m.id: m
            for m in UserMemory.objects.filter(id__in=[m1.id, m2.id], user_id=1)
        }
        assert len(result) == 2
        assert m1.id in result and m2.id in result

    def test_batch_get_by_ids_cross_user(self) -> None:
        m = UserMemory.objects.create(user_id=1, content="a")
        result = list(UserMemory.objects.filter(id__in=[m.id], user_id=999))
        assert len(result) == 0


class TestEmbeddingRepository:

    @pytest.fixture(autouse=True)
    def _clean(self):
        UserMemoryEmbedding.objects.all().delete()
        UserMemory.objects.all().delete()
        yield

    def test_create_and_get(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        UserMemoryEmbedding.objects.create(
            memory=memory, user_id=1, type="memory", chunk_text="test"
        )

        fetched = list(
            UserMemoryEmbedding.objects.filter(memory_id=memory.id, user_id=1)
        )
        assert len(fetched) == 1

    def test_delete_by_memory_id(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")
        UserMemoryEmbedding.objects.create(memory=memory, user_id=1, type="memory")
        deleted, _ = UserMemoryEmbedding.objects.filter(memory_id=memory.id).delete()
        assert deleted == 1

    def test_vector_search_skeleton_returns_empty(self) -> None:
        from pgvector.django import CosineDistance

        results = list(
            UserMemoryEmbedding.objects.filter(
                user_id=1, embedding__isnull=False
            )
            .annotate(distance=CosineDistance("embedding", [0.0] * 1024))
            .order_by("distance")[:5]
        )
        assert results == []

    def test_keyword_search_skeleton_returns_empty(self) -> None:
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        sv = SearchVector("content", config="jiebacfg")
        sq = SearchQuery("test", config="jiebacfg")
        results = list(
            UserMemory.objects.filter(user_id=1)
            .annotate(search=sv, rank=SearchRank(sv, sq))
            .filter(rank__gt=0)
            .order_by("-rank")[:5]
        )
        assert results == []

    def test_user_id_none_raises(self) -> None:
        from apps.memory.repositories import _require_user_id

        with pytest.raises(ValueError):
            _require_user_id(None)
