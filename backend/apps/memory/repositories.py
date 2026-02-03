"""记忆仓库层 — 所有查询必须包含 user_id 过滤 [R-004]"""

from datetime import date, datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.utils import timezone

from apps.memory.models import UserMemory, UserMemoryEmbedding


def _require_user_id(user_id: Optional[int]) -> None:
    if user_id is None:
        raise ValueError("user_id 不可为空，所有操作必须按用户隔离")


class MemoryRepository:

    @staticmethod
    @sync_to_async
    def create(memory: UserMemory) -> UserMemory:
        memory.save()
        return memory

    @staticmethod
    @sync_to_async
    def get_by_id(memory_id: int, user_id: int) -> Optional[UserMemory]:
        _require_user_id(user_id)
        try:
            return UserMemory.objects.get(id=memory_id, user_id=user_id)
        except UserMemory.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_user_id(user_id: int) -> list[UserMemory]:
        _require_user_id(user_id)
        return list(UserMemory.objects.filter(user_id=user_id))

    @staticmethod
    @sync_to_async
    def batch_get_by_ids(memory_ids: list[int], user_id: int) -> dict[int, UserMemory]:
        _require_user_id(user_id)
        return {m.id: m for m in UserMemory.objects.filter(id__in=memory_ids, user_id=user_id)}

    @staticmethod
    @sync_to_async
    def update(memory: UserMemory) -> UserMemory:
        memory.save()
        return memory

    @staticmethod
    @sync_to_async
    def delete(memory_id: int, user_id: int) -> bool:
        _require_user_id(user_id)
        deleted, _ = UserMemory.objects.filter(id=memory_id, user_id=user_id).delete()
        return deleted > 0

    @staticmethod
    @sync_to_async
    def list_by_user(
        user_id: int, type_filter: Optional[str] = None,
        page: int = 1, page_size: int = 20,
    ) -> tuple[list[UserMemory], int]:
        _require_user_id(user_id)
        qs = UserMemory.objects.filter(user_id=user_id)
        if type_filter:
            qs = qs.filter(type=type_filter)
        total = qs.count()
        offset = (page - 1) * page_size
        return list(qs[offset : offset + page_size]), total

    @staticmethod
    @sync_to_async
    def find_retryable(max_retry: int = 3) -> list[UserMemory]:
        return list(UserMemory.objects.filter(
            embedding_status=UserMemory.EmbeddingStatus.FAILED, retry_count__lt=max_retry,
        ))

    @staticmethod
    @sync_to_async
    def find_pending_timeout(timeout_seconds: int = 300) -> list[UserMemory]:
        threshold = timezone.now() - timezone.timedelta(seconds=timeout_seconds)
        return list(UserMemory.objects.filter(
            embedding_status=UserMemory.EmbeddingStatus.PENDING, updated_at__lt=threshold,
        ))

    @staticmethod
    @sync_to_async
    def find_by_type_and_date_range(user_id: int, type: str, start_date, end_date) -> list[UserMemory]:
        _require_user_id(user_id)
        return list(UserMemory.objects.filter(
            user_id=user_id, type=type, created_at__gte=start_date, created_at__lt=end_date,
        ))

    @staticmethod
    @sync_to_async
    def find_active_users_for_daily(target_date: date) -> list[int]:
        next_day = target_date + timezone.timedelta(days=1)
        return list(UserMemory.objects.filter(
            type=UserMemory.MemoryType.MEMORY, created_at__gte=target_date, created_at__lt=next_day,
        ).values_list("user_id", flat=True).distinct())

    @staticmethod
    @sync_to_async
    def find_active_users_for_monthly(year: int, month: int) -> list[int]:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return list(UserMemory.objects.filter(
            type=UserMemory.MemoryType.MEMORY, created_at__gte=start, created_at__lt=end,
        ).values_list("user_id", flat=True).distinct())


class EmbeddingRepository:

    @staticmethod
    @sync_to_async
    def create(embedding: UserMemoryEmbedding) -> UserMemoryEmbedding:
        embedding.save()
        return embedding

    @staticmethod
    @sync_to_async
    def delete_by_memory_id(memory_id: int) -> int:
        deleted, _ = UserMemoryEmbedding.objects.filter(memory_id=memory_id).delete()
        return deleted

    @staticmethod
    @sync_to_async
    def get_by_memory_id(memory_id: int, user_id: int) -> list[UserMemoryEmbedding]:
        _require_user_id(user_id)
        return list(UserMemoryEmbedding.objects.filter(memory_id=memory_id, user_id=user_id))

    @staticmethod
    @sync_to_async
    def vector_search(user_id: int, query_embedding: list[float], limit: int = 5) -> list[tuple[int, float]]:
        from pgvector.django import CosineDistance
        _require_user_id(user_id)
        results = (
            UserMemoryEmbedding.objects.filter(
                user_id=user_id, embedding__isnull=False,
                memory__embedding_status=UserMemory.EmbeddingStatus.DONE,
            )
            .annotate(distance=CosineDistance("embedding", query_embedding))
            .order_by("distance")[:limit]
        )
        return [(r.memory_id, 1.0 - float(r.distance)) for r in results]

    @staticmethod
    @sync_to_async
    def keyword_search(user_id: int, query_text: str, limit: int = 5) -> list[tuple[int, float]]:
        _require_user_id(user_id)
        if not query_text or not query_text.strip():
            return []
        sv = SearchVector("content", config="jiebacfg")
        sq = SearchQuery(query_text, config="jiebacfg")
        results = (
            UserMemory.objects.filter(user_id=user_id)
            .annotate(search=sv, rank=SearchRank(sv, sq))
            .filter(rank__gt=0)
            .order_by("-rank")[:limit]
        )
        return [(r.id, float(r.rank)) for r in results]


memory_repo = MemoryRepository()
embedding_repo = EmbeddingRepository()
