import asyncio
import logging

from celery import shared_task
from django.conf import settings

from apps.memory.task_helpers import has_active_users, run_summary, warmup_language_model

logger = logging.getLogger(__name__)


@shared_task(name="memory.generate_embedding")
def generate_embedding(memory_id: int) -> None:
    from apps.memory.models import UserMemory, UserMemoryEmbedding
    if has_active_users():
        logger.info("Skipping embedding: active users (memory_id=%d)", memory_id); return
    try:
        memory = UserMemory.objects.get(id=memory_id)
    except UserMemory.DoesNotExist:
        return
    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY
    if memory.retry_count >= max_retry:
        memory.embedding_status = UserMemory.EmbeddingStatus.FAILED
        memory.save(update_fields=["embedding_status", "updated_at"]); return
    memory.embedding_status = UserMemory.EmbeddingStatus.PROCESSING
    memory.save(update_fields=["embedding_status", "updated_at"])
    try:
        from apps.memory.services import EmbeddingClient
        loop = asyncio.new_event_loop()
        try:
            vector = loop.run_until_complete(EmbeddingClient.generate_embedding(memory.content))
        finally:
            loop.close()
        UserMemoryEmbedding.objects.filter(memory_id=memory_id).delete()
        UserMemoryEmbedding.objects.create(
            memory=memory, user_id=memory.user_id, type=memory.type,
            name=memory.name, chunk_index=0, chunk_text=memory.content, embedding=vector,
        )
        memory.embedding_status = UserMemory.EmbeddingStatus.DONE
        memory.save(update_fields=["embedding_status", "updated_at"])
        logger.info("Embedding generated: memory_id=%d", memory_id)
    except Exception as e:
        memory.embedding_status = UserMemory.EmbeddingStatus.FAILED; memory.retry_count += 1
        memory.save(update_fields=["embedding_status", "retry_count", "updated_at"])
        logger.warning("Embedding failed (retry %d/%d): memory_id=%d: %s", memory.retry_count, max_retry, memory_id, e)
        return
    try:
        warmup_language_model()
    except Exception as e:
        logger.warning("Language model warmup error (non-fatal): %s", e)


@shared_task(name="memory.retry_failed_embeddings")
def retry_failed_embeddings() -> None:
    from django.utils import timezone
    from apps.memory.models import UserMemory
    if has_active_users():
        logger.info("Skipping retry scan: active users detected"); return
    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY
    threshold = timezone.now() - timezone.timedelta(seconds=settings.MEMORY_EMBEDDING_PENDING_TIMEOUT)
    retryable = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.FAILED, retry_count__lt=max_retry,
    ) | UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PENDING, updated_at__lt=threshold,
    )
    count = sum(1 for m in retryable if not generate_embedding.delay(m.id) or True)
    if count: logger.info("Retry scan: %d records dispatched", count)


@shared_task(name="memory.generate_daily_summary")
def generate_daily_summary() -> None:
    from django.utils import timezone
    from apps.memory.models import UserMemory
    today = timezone.now().date()
    yesterday = today - timezone.timedelta(days=1)
    start = timezone.make_aware(timezone.datetime.combine(yesterday, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    run_summary(UserMemory.MemoryType.COMPACTION, start, end, "daily-summary", f"daily-{yesterday.isoformat()}", 100)


@shared_task(name="memory.generate_monthly_summary")
def generate_monthly_summary() -> None:
    from django.utils import timezone
    from apps.memory.models import UserMemory
    now = timezone.now()
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    start = timezone.make_aware(timezone.datetime(year, month, 1))
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = timezone.make_aware(timezone.datetime(end_year, end_month, 1))
    run_summary(UserMemory.MemoryType.DAILY_SUMMARY, start, end, "monthly-summary", f"monthly-{year}-{month:02d}", 200)


@shared_task(name="memory.embedding_health_check")
def embedding_health_check() -> None:
    from django.utils import timezone as tz
    from apps.memory.models import UserMemory
    now = tz.now()
    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY
    retry_count = 0
    for mem in UserMemory.objects.filter(embedding_status=UserMemory.EmbeddingStatus.FAILED, retry_count__lt=max_retry):
        mem.embedding_status = UserMemory.EmbeddingStatus.PENDING; mem.retry_count += 1
        mem.save(update_fields=["embedding_status", "retry_count", "updated_at"]); retry_count += 1
    stuck_pending = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PENDING, updated_at__lt=now - tz.timedelta(hours=1),
    ).update(embedding_status=UserMemory.EmbeddingStatus.FAILED)
    stuck_processing = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PROCESSING, updated_at__lt=now - tz.timedelta(minutes=10),
    ).update(embedding_status=UserMemory.EmbeddingStatus.FAILED)
    total_failed = UserMemory.objects.filter(embedding_status=UserMemory.EmbeddingStatus.FAILED).count()
    msg = f"Embedding health check: retried={retry_count}, stuck_pending={stuck_pending}, stuck_processing={stuck_processing}, total_failed={total_failed}"
    logger.error(msg) if total_failed > 10 else logger.info(msg)
