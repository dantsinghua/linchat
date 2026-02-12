"""
Celery 异步任务 — 媒体文件过期清理

参考:
- specs/008-multimodal-minicpm/tasks.md T065
- 宪法 1.3 失败补偿机制
"""

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# 连续失败阈值，超过此数终止本轮清理（疑似 MinIO 不可达）
MAX_CONSECUTIVE_FAILURES = 10


@shared_task(name="chat.clean_expired_media")
def clean_expired_media() -> dict:
    """清理过期媒体文件

    清理逻辑:
    1. 查询 expires_at < now 且 is_expired=False 的 MediaAttachment 记录
    2. 逐条删除 MinIO 中对应的原始文件
    3. 删除成功后更新 is_expired=True
    4. 记录清理日志

    失败补偿 (宪法 1.3):
    - 单条 MinIO 删除失败: 记录 ERROR 日志并跳过，is_expired 保持 False，下次自动重试
    - 连续 10 条失败: 发出 CRITICAL 告警并终止本轮清理（疑似 MinIO 不可达）

    Returns:
        清理统计 {total, cleaned, failed, aborted}
    """
    from apps.chat.models import MediaAttachment
    from apps.chat.services.minio_service import minio_service

    now = timezone.now()
    # 单轮最大处理量，失败记录留到下次定时任务自动重试
    batch_limit = 1000
    total_cleaned = 0
    total_failed = 0
    consecutive_failures = 0
    aborted = False

    # 查询所有过期但未标记的附件（单轮处理，避免重复查询失败记录）
    expired_attachments = list(
        MediaAttachment.objects.filter(
            expires_at__lt=now, is_expired=False
        )[:batch_limit]
    )

    for attachment in expired_attachments:
        # 检查连续失败阈值
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "媒体清理任务中止: 连续 %d 条 MinIO 删除失败，疑似 MinIO 不可达",
                consecutive_failures,
            )
            aborted = True
            break

        # 尝试删除 MinIO 文件
        delete_success = minio_service.delete_file(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=attachment.storage_path,
        )

        if delete_success:
            # 标记为已过期
            attachment.is_expired = True
            attachment.save(update_fields=["is_expired"])
            total_cleaned += 1
            consecutive_failures = 0
            logger.debug(
                "清理过期媒体: uuid=%s, path=%s",
                attachment.attachment_uuid,
                attachment.storage_path,
            )
        else:
            total_failed += 1
            consecutive_failures += 1
            logger.error(
                "MinIO 删除失败，跳过: uuid=%s, path=%s",
                attachment.attachment_uuid,
                attachment.storage_path,
            )

    stats = {
        "total": total_cleaned + total_failed,
        "cleaned": total_cleaned,
        "failed": total_failed,
        "aborted": aborted,
    }

    logger.info(
        "媒体清理任务完成: cleaned=%d, failed=%d, aborted=%s",
        total_cleaned,
        total_failed,
        aborted,
    )

    return stats
