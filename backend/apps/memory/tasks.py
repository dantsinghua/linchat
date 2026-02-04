"""Celery 异步任务 — embedding 生成、重试扫描、定时总结"""

import asyncio
import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


def _has_active_users() -> bool:
    """检查 Redis 中是否存在活跃 token（1小时无操作自动过期）

    Token 有 AUTH_TOKEN_IDLE_TTL=3600 的 TTL，超过 1 小时无操作自动过期。
    存在任何 auth:token:* 键即表示有活跃用户，此时应推迟 embedding 任务
    以避免单 GPU 模型热切换。
    """
    import redis as redis_lib

    r = redis_lib.from_url(settings.REDIS_URL)
    cursor, keys = r.scan(cursor=0, match="auth:token:*", count=10)
    return len(keys) > 0


def _warmup_language_model() -> None:
    """向语言模型 API 发送最小请求，强制 vLLM 加载语言模型回 GPU"""
    import httpx
    from apps.models.services import model_service

    try:
        config = model_service.get_active_model("language")
        if not config:
            return
        client = httpx.Client(timeout=60.0)
        response = client.post(
            f"{config['url']}/chat/completions",
            headers={"Authorization": f"Bearer {config['api_key'] or 'not-needed'}"},
            json={
                "model": config["name"],
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
        )
        response.raise_for_status()
        logger.info("Language model warmup completed")
    except Exception as e:
        logger.warning("Language model warmup failed: %s", e)


@shared_task(name="memory.generate_embedding")
def generate_embedding(memory_id: int) -> None:
    """生成 embedding: pending → processing → done/failed

    有活跃用户时跳过执行（保持 pending 状态），避免单 GPU 模型热切换。
    """
    from apps.memory.models import UserMemory, UserMemoryEmbedding

    # 活跃用户检查：有用户在线时推迟 embedding，避免 GPU 模型切换
    if _has_active_users():
        logger.info(
            "Skipping embedding generation: active users detected (memory_id=%d)",
            memory_id,
        )
        return  # 不改状态，下次 retry_failed_embeddings 会重新投递

    try:
        memory = UserMemory.objects.get(id=memory_id)
    except UserMemory.DoesNotExist:
        return

    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY
    if memory.retry_count >= max_retry:
        memory.embedding_status = UserMemory.EmbeddingStatus.FAILED
        memory.save(update_fields=["embedding_status", "updated_at"])
        return

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
            name=memory.name, chunk_index=0, chunk_text=memory.content,
            embedding=vector,
        )
        memory.embedding_status = UserMemory.EmbeddingStatus.DONE
        memory.save(update_fields=["embedding_status", "updated_at"])
        logger.info("Embedding generated: memory_id=%d", memory_id)
    except Exception as e:
        memory.embedding_status = UserMemory.EmbeddingStatus.FAILED
        memory.retry_count += 1
        memory.save(update_fields=["embedding_status", "retry_count", "updated_at"])
        logger.warning(
            "Embedding failed (retry %d/%d): memory_id=%d: %s",
            memory.retry_count, max_retry, memory_id, e,
        )
        return

    # 成功后预热语言模型，强制 vLLM 将语言模型加载回 GPU（预热失败不影响 embedding 结果）
    try:
        _warmup_language_model()
    except Exception as e:
        logger.warning("Language model warmup error (non-fatal): %s", e)


@shared_task(name="memory.retry_failed_embeddings")
def retry_failed_embeddings() -> None:
    """定时扫描 failed(retry<3) 和超时 pending 记录，重新投递

    有活跃用户时跳过整个扫描周期，避免投递的任务触发 GPU 模型切换。
    """
    from django.utils import timezone
    from apps.memory.models import UserMemory

    # 活跃用户检查
    if _has_active_users():
        logger.info("Skipping retry scan: active users detected")
        return

    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY
    threshold = timezone.now() - timezone.timedelta(seconds=settings.MEMORY_EMBEDDING_PENDING_TIMEOUT)

    retryable = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.FAILED, retry_count__lt=max_retry,
    ) | UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PENDING, updated_at__lt=threshold,
    )

    count = 0
    for memory in retryable:
        generate_embedding.delay(memory.id)
        count += 1

    if count:
        logger.info("Retry scan: %d records dispatched", count)


def _run_summary(primary_type: str, start, end, summary_type: str, summary_name: str, msg_limit: int) -> None:
    """定时总结公共逻辑: 查找活跃用户 → 收集内容(primary → message fallback) → summarize"""
    from apps.memory.models import UserMemory
    from apps.memory.services import MemoryService

    active_user_ids = set(
        UserMemory.objects.filter(type=primary_type, created_at__gte=start, created_at__lt=end)
        .values_list("user_id", flat=True).distinct()
    )

    try:
        from apps.chat.models import Message
        active_user_ids.update(
            Message.objects.filter(created_time__gte=start, created_time__lt=end)
            .values_list("user_id", flat=True).distinct()
        )
    except Exception as e:
        logger.warning("Failed to query message users: %s", e)

    if not active_user_ids:
        logger.info("No active users for %s", summary_name)
        return

    logger.info("%s: %d active users", summary_type, len(active_user_ids))

    loop = asyncio.new_event_loop()
    try:
        for user_id in active_user_ids:
            try:
                # 优先从 primary_type 记忆获取
                records = UserMemory.objects.filter(
                    user_id=user_id, type=primary_type,
                    created_at__gte=start, created_at__lt=end,
                )
                content = "\n\n".join(r.content for r in records)

                # 降级到 message 表
                if not content:
                    try:
                        from apps.chat.models import Message
                        msgs = Message.objects.filter(
                            user_id=user_id, created_time__gte=start, created_time__lt=end,
                        ).order_by("created_time")[:msg_limit]
                        content = "\n".join(f"{m.role}: {m.content}" for m in msgs)
                    except Exception as e:
                        logger.warning("Failed to get messages for user %d: %s", user_id, e)

                if not content:
                    continue

                loop.run_until_complete(MemoryService.summarize_and_store(
                    user_id=user_id, content=content,
                    summary_type=summary_type, summary_name=summary_name,
                ))
            except Exception as e:
                logger.warning("%s failed for user %d: %s", summary_type, user_id, e)
    finally:
        loop.close()


@shared_task(name="memory.generate_daily_summary")
def generate_daily_summary() -> None:
    """每日记忆总结: compaction → message → 跳过"""
    from django.utils import timezone
    from apps.memory.models import UserMemory

    today = timezone.now().date()
    yesterday = today - timezone.timedelta(days=1)
    start = timezone.make_aware(timezone.datetime.combine(yesterday, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))

    _run_summary(
        UserMemory.MemoryType.COMPACTION, start, end,
        "daily-summary", f"daily-{yesterday.isoformat()}", msg_limit=100,
    )


@shared_task(name="memory.generate_monthly_summary")
def generate_monthly_summary() -> None:
    """每月记忆总结: daily-summary → message → 跳过"""
    from django.utils import timezone
    from apps.memory.models import UserMemory

    now = timezone.now()
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    start = timezone.make_aware(timezone.datetime(year, month, 1))
    end_month = 1 if month == 12 else month + 1
    end_year = year + 1 if month == 12 else year
    end = timezone.make_aware(timezone.datetime(end_year, end_month, 1))

    _run_summary(
        UserMemory.MemoryType.DAILY_SUMMARY, start, end,
        "monthly-summary", f"monthly-{year}-{month:02d}", msg_limit=200,
    )


@shared_task(name="memory.embedding_health_check")
def embedding_health_check() -> None:
    """Embedding 健康检查 — 每小时执行

    1. 重置 failed + retry_count < 3 的记录为 pending（retry_count+1）
    2. 标记 pending 超 1 小时和 processing 超 10 分钟的记录为 failed
    3. 失败数 > 10 时输出 ERROR 级别告警
    """
    from django.utils import timezone as tz

    from apps.memory.models import UserMemory

    now = tz.now()
    pending_threshold = now - tz.timedelta(hours=1)
    processing_threshold = now - tz.timedelta(minutes=10)
    max_retry = settings.MEMORY_EMBEDDING_MAX_RETRY

    # 1. 重置可重试的 failed 记录
    retryable = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.FAILED,
        retry_count__lt=max_retry,
    )
    retry_count = 0
    for mem in retryable:
        mem.embedding_status = UserMemory.EmbeddingStatus.PENDING
        mem.retry_count += 1
        mem.save(update_fields=["embedding_status", "retry_count", "updated_at"])
        retry_count += 1

    # 2. 标记超时的 pending 记录
    stuck_pending = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PENDING,
        updated_at__lt=pending_threshold,
    ).update(embedding_status=UserMemory.EmbeddingStatus.FAILED)

    # 3. 标记超时的 processing 记录
    stuck_processing = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.PROCESSING,
        updated_at__lt=processing_threshold,
    ).update(embedding_status=UserMemory.EmbeddingStatus.FAILED)

    # 4. 统计当前失败总数
    total_failed = UserMemory.objects.filter(
        embedding_status=UserMemory.EmbeddingStatus.FAILED,
    ).count()

    # 5. 输出汇总日志
    summary = (
        f"Embedding health check: retried={retry_count}, "
        f"stuck_pending={stuck_pending}, stuck_processing={stuck_processing}, "
        f"total_failed={total_failed}"
    )

    if total_failed > 10:
        logger.error(summary)
    else:
        logger.info(summary)
