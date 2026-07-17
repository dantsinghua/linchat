import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 10


@shared_task(name="media.clean_expired_media")
def clean_expired_media() -> dict:
    """媒体过期清理 — 仅删除原始文件，保留解析结果和 chunk embeddings"""
    from apps.common.storage.minio_service import minio_service
    from apps.media.models import MediaAttachment

    now = timezone.now()
    total_cleaned, total_failed, consecutive_failures, aborted = 0, 0, 0, False

    expired_attachments = list(MediaAttachment.objects.filter(expires_at__lt=now, is_expired=False)[:1000])
    for attachment in expired_attachments:
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical("媒体清理任务中止: 连续 %d 条 MinIO 删除失败", consecutive_failures)
            aborted = True
            break
        # T018: 仅删除原始文件(storage_path)，保留 parsed_content_path 备份
        if minio_service.delete_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=attachment.storage_path):
            attachment.is_expired = True
            attachment.save(update_fields=["is_expired"])
            total_cleaned += 1
            consecutive_failures = 0
        else:
            total_failed += 1
            consecutive_failures += 1

    logger.info("媒体清理任务完成: cleaned=%d, failed=%d, aborted=%s", total_cleaned, total_failed, aborted)
    return {"total": total_cleaned + total_failed, "cleaned": total_cleaned, "failed": total_failed, "aborted": aborted}


# --- 011-document-subagent-rag: Embedding Celery 任务 ---


@shared_task(name="media.generate_document_embeddings")
def generate_document_embeddings(attachment_id: int) -> None:
    """分块 + 生成 Embedding 向量 — Celery 异步任务"""
    from apps.media.models import MediaAttachment
    from apps.media.repositories import doc_chunk_repo, media_attachment_repo
    from apps.media.services.document_rag import chunk_document
    from apps.memory.task_helpers import has_active_users

    try:
        attachment = MediaAttachment.objects.get(attachment_id=attachment_id)
    except MediaAttachment.DoesNotExist:
        logger.warning("Doc embedding: attachment not found id=%d", attachment_id)
        return

    if not attachment.parsed_content:
        logger.warning("Doc embedding: no parsed_content id=%d", attachment_id)
        return

    # GPU 互斥: 有活跃用户时跳过（Celery Beat 会重试）
    if has_active_users():
        logger.info("Doc embedding skipped (active users): id=%d", attachment_id)
        return

    # 标记为 processing
    async_to_sync(media_attachment_repo.update_embedding_status)(attachment_id, "processing")

    try:
        chunk_size = getattr(settings, "DOC_CHUNK_SIZE", 800)
        chunk_overlap = getattr(settings, "DOC_CHUNK_OVERLAP", 100)
        chunks = chunk_document(attachment.parsed_content, chunk_size, chunk_overlap)

        if not chunks:
            logger.warning("Doc embedding: no chunks generated id=%d", attachment_id)
            async_to_sync(media_attachment_repo.update_embedding_status)(attachment_id, "done")
            return

        # 删除旧 chunks
        async_to_sync(doc_chunk_repo.delete_by_attachment_id)(attachment_id)

        # 逐块生成 embedding
        from apps.media.models import DocumentChunkEmbedding
        from apps.memory.services import EmbeddingClient

        chunk_objects = []
        for idx, chunk_text in enumerate(chunks):
            try:
                embedding = async_to_sync(EmbeddingClient.generate_embedding)(chunk_text)
            except Exception as e:
                logger.warning("Doc embedding chunk failed: id=%d, chunk=%d, err=%s", attachment_id, idx, e)
                embedding = None

            chunk_objects.append(DocumentChunkEmbedding(
                attachment=attachment,
                user_id=attachment.user_id,
                chunk_index=idx,
                chunk_text=chunk_text,
                embedding=embedding,
            ))

        # 批量插入
        async_to_sync(doc_chunk_repo.bulk_create_chunks)(chunk_objects)
        async_to_sync(media_attachment_repo.update_embedding_status)(attachment_id, "done")
        logger.info("Doc embedding done: id=%d, chunks=%d", attachment_id, len(chunk_objects))

    except Exception as e:
        logger.error("Doc embedding failed: id=%d, err=%s", attachment_id, e)
        async_to_sync(media_attachment_repo.update_embedding_status)(attachment_id, "failed")

    # 预热语言模型（归还 GPU）
    try:
        from apps.memory.task_helpers import warmup_language_model
        warmup_language_model()
    except Exception as e:
        logger.warning("Doc embedding warmup failed: %s", e)


@shared_task(name="media.retry_failed_doc_embeddings")
def retry_failed_doc_embeddings() -> None:
    """重试失败的文档 Embedding — 每 5 分钟扫描"""
    from apps.media.models import MediaAttachment

    failed = list(MediaAttachment.objects.filter(embedding_status="failed").values_list("attachment_id", flat=True)[:50])
    for att_id in failed:
        generate_document_embeddings.delay(att_id)

    if failed:
        logger.info("Doc embedding retry dispatched: count=%d", len(failed))
