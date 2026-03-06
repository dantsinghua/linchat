from datetime import datetime
from functools import reduce
from typing import Optional

from asgiref.sync import sync_to_async
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Q
from django.utils import timezone

from apps.media.models import DocumentChunkEmbedding, MediaAttachment


class MediaAttachmentRepository:
    @staticmethod
    @sync_to_async
    def create(attachment: MediaAttachment) -> MediaAttachment:
        attachment.save()
        return attachment

    @staticmethod
    @sync_to_async
    def get_by_uuid(attachment_uuid: str, user_id: int) -> Optional[MediaAttachment]:
        try:
            return MediaAttachment.objects.get(attachment_uuid=attachment_uuid, user_id=user_id)
        except MediaAttachment.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuid_any_user(attachment_uuid: str) -> Optional[MediaAttachment]:
        try:
            return MediaAttachment.objects.get(attachment_uuid=attachment_uuid)
        except MediaAttachment.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuids(attachment_uuids: list[str], user_id: int) -> list[MediaAttachment]:
        return list(MediaAttachment.objects.filter(attachment_uuid__in=attachment_uuids, user_id=user_id))

    @staticmethod
    @sync_to_async
    def update(attachment: MediaAttachment) -> MediaAttachment:
        attachment.save()
        return attachment

    @staticmethod
    @sync_to_async
    def associate_message(attachment_ids: list[int], message_id: int, user_id: int) -> int:
        return MediaAttachment.objects.filter(attachment_id__in=attachment_ids, user_id=user_id).update(message_id=message_id)

    @staticmethod
    @sync_to_async
    def find_expired(before_date: datetime, limit: int = 100) -> list[MediaAttachment]:
        return list(MediaAttachment.objects.filter(expires_at__lt=before_date, is_expired=False)[:limit])

    @staticmethod
    @sync_to_async
    def mark_expired(attachment_ids: list[int]) -> int:
        return MediaAttachment.objects.filter(attachment_id__in=attachment_ids).update(is_expired=True)

    # --- 011-document-subagent-rag: 文档查询方法 ---

    @staticmethod
    @sync_to_async
    def search_documents(
        user_id: int,
        file_name: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        has_parsed: bool | None = None,
        order_by: str = "-created_at",
        limit: int = 20,
    ) -> list[MediaAttachment]:
        """文档列表查询 — 不过滤 is_expired（解析结果在过期后仍可查询）"""
        qs = MediaAttachment.objects.filter(media_type="document", user_id=user_id)
        if file_name:
            keywords = file_name.strip().split()
            if keywords:
                qs = qs.filter(reduce(lambda a, b: a & b, [Q(file_name__icontains=kw) for kw in keywords]))
        if created_after:
            qs = qs.filter(created_at__gte=created_after)
        if created_before:
            qs = qs.filter(created_at__lt=created_before)
        if has_parsed is True:
            qs = qs.filter(parsed_content__isnull=False)
        elif has_parsed is False:
            qs = qs.filter(parsed_content__isnull=True)
        order_map = {"newest": "-created_at", "oldest": "created_at", "name": "file_name", "size": "-file_size"}
        qs = qs.order_by(order_map.get(order_by, order_by))
        return list(qs[:limit])

    @staticmethod
    @sync_to_async
    def update_parsed_cache(
        attachment_id: int,
        parsed_content: str,
        parsed_content_path: str,
        parsed_at: datetime,
        parsed_content_size: int,
    ) -> int:
        """原子更新 5 个解析缓存字段 + embedding_status='pending'"""
        return MediaAttachment.objects.filter(attachment_id=attachment_id).update(
            parsed_content=parsed_content,
            parsed_content_path=parsed_content_path,
            parsed_at=parsed_at,
            parsed_content_size=parsed_content_size,
            embedding_status=MediaAttachment.EMBEDDING_STATUS_PENDING,
        )

    @staticmethod
    @sync_to_async
    def update_embedding_status(attachment_id: int, status: str) -> int:
        return MediaAttachment.objects.filter(attachment_id=attachment_id).update(embedding_status=status)

    @staticmethod
    @sync_to_async
    def clear_parsed_cache(attachment_id: int) -> int:
        """清除解析缓存字段并重置 embedding_status"""
        return MediaAttachment.objects.filter(attachment_id=attachment_id).update(
            parsed_content=None,
            parsed_content_path=None,
            parsed_at=None,
            parsed_content_size=None,
            embedding_status=MediaAttachment.EMBEDDING_STATUS_NONE,
        )

    @staticmethod
    @sync_to_async
    def fulltext_search_parsed_content(
        user_id: int,
        query_text: str,
        limit: int = 10,
    ) -> list[tuple[int, str, str, float]]:
        """降级搜索: chunk 未生成时直接搜索 parsed_content GIN 索引"""
        if not query_text or not query_text.strip():
            return []
        sv = SearchVector("parsed_content", config="jiebacfg")
        sq = SearchQuery(query_text, config="jiebacfg")
        results = (
            MediaAttachment.objects.filter(
                media_type="document", user_id=user_id, parsed_content__isnull=False
            )
            .annotate(search=sv, rank=SearchRank(sv, sq))
            .filter(rank__gt=0)
            .order_by("-rank")[:limit]
        )
        return [(r.attachment_id, r.attachment_uuid, r.file_name, float(r.rank)) for r in results]


class DocumentChunkEmbeddingRepository:
    @staticmethod
    @sync_to_async
    def bulk_create_chunks(chunks: list[DocumentChunkEmbedding]) -> list[DocumentChunkEmbedding]:
        return DocumentChunkEmbedding.objects.bulk_create(chunks)

    @staticmethod
    @sync_to_async
    def delete_by_attachment_id(attachment_id: int) -> int:
        deleted, _ = DocumentChunkEmbedding.objects.filter(attachment_id=attachment_id).delete()
        return deleted

    @staticmethod
    @sync_to_async
    def vector_search(
        user_id: int,
        query_embedding: list[float],
        limit: int = 10,
    ) -> list[tuple[int, int, str, float]]:
        """向量搜索 — CosineDistance 排序，返回 (attachment_id, chunk_index, chunk_text, score)"""
        from pgvector.django import CosineDistance
        results = (
            DocumentChunkEmbedding.objects.filter(user_id=user_id, embedding__isnull=False)
            .annotate(distance=CosineDistance("embedding", query_embedding))
            .order_by("distance")[:limit]
        )
        return [(r.attachment_id, r.chunk_index, r.chunk_text, 1.0 - float(r.distance)) for r in results]

    @staticmethod
    @sync_to_async
    def keyword_search(
        user_id: int,
        query_text: str,
        limit: int = 10,
    ) -> list[tuple[int, int, str, float]]:
        """关键词搜索 — jiebacfg 全文检索，返回 (attachment_id, chunk_index, chunk_text, score)"""
        if not query_text or not query_text.strip():
            return []
        sv = SearchVector("chunk_text", config="jiebacfg")
        sq = SearchQuery(query_text, config="jiebacfg")
        results = (
            DocumentChunkEmbedding.objects.filter(user_id=user_id)
            .annotate(search=sv, rank=SearchRank(sv, sq))
            .filter(rank__gt=0)
            .order_by("-rank")[:limit]
        )
        return [(r.attachment_id, r.chunk_index, r.chunk_text, float(r.rank)) for r in results]


media_attachment_repo = MediaAttachmentRepository()
doc_chunk_repo = DocumentChunkEmbeddingRepository()
