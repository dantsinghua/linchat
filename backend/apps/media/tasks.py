import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 10


@shared_task(name="media.clean_expired_media")
def clean_expired_media() -> dict:
    from apps.media.models import MediaAttachment
    from apps.common.storage.minio_service import minio_service

    now = timezone.now()
    total_cleaned, total_failed, consecutive_failures, aborted = 0, 0, 0, False

    expired_attachments = list(MediaAttachment.objects.filter(expires_at__lt=now, is_expired=False)[:1000])
    for attachment in expired_attachments:
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical("媒体清理任务中止: 连续 %d 条 MinIO 删除失败", consecutive_failures)
            aborted = True
            break
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
