import logging
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from apps.media.models import MediaAttachment

logger = logging.getLogger(__name__)


async def get_cached_result(attachment: "MediaAttachment") -> str | None:
    if attachment.parsed_content:
        return attachment.parsed_content
    if attachment.parsed_content_path:
        try:
            from apps.common.storage.minio_service import minio_service
            data = minio_service.download_file(
                bucket=settings.MINIO_BUCKET_MEDIA,
                object_name=attachment.parsed_content_path,
            )
            content = data.decode("utf-8")
            logger.info("Doc cache fallback MinIO: attachment=%d, path=%s", attachment.attachment_id, attachment.parsed_content_path)
            return content
        except Exception as e:
            logger.warning("Doc cache MinIO fallback failed: attachment=%d, err=%s", attachment.attachment_id, e)
    return None


async def save_parsed_result(attachment: "MediaAttachment", content: str) -> bool:
    from datetime import date as _date

    from apps.common.storage.minio_service import minio_service
    from apps.media.repositories import media_attachment_repo

    minio_path = f"parsed/{attachment.user_id}/{_date.today().isoformat()}/{attachment.attachment_uuid}.md"
    content_bytes = content.encode("utf-8")

    try:
        minio_service.upload_bytes(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=minio_path,
            data=content_bytes,
            content_type="text/markdown; charset=utf-8",
        )
    except Exception as e:
        logger.error("Doc cache MinIO upload failed: attachment=%d, err=%s", attachment.attachment_id, e)
        return False

    from django.utils import timezone as tz

    try:
        updated = await media_attachment_repo.update_parsed_cache(
            attachment_id=attachment.attachment_id,
            parsed_content=content,
            parsed_content_path=minio_path,
            parsed_at=tz.now(),
            parsed_content_size=len(content_bytes),
        )
        if updated == 0:
            logger.warning("Doc cache DB update returned 0 rows: attachment=%d", attachment.attachment_id)
    except Exception as e:
        logger.error("Doc cache DB update failed, compensating MinIO delete: attachment=%d, err=%s", attachment.attachment_id, e)
        minio_service.delete_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=minio_path)
        return False

    try:
        from apps.media.tasks import generate_document_embeddings
        generate_document_embeddings.delay(attachment.attachment_id)
        logger.info("Doc cache saved + embedding dispatched: attachment=%d, size=%d", attachment.attachment_id, len(content_bytes))
    except Exception as e:
        logger.warning("Doc embedding dispatch failed (non-blocking): attachment=%d, err=%s", attachment.attachment_id, e)

    return True


async def clear_parsed_cache(attachment: "MediaAttachment") -> None:
    from apps.common.storage.minio_service import minio_service
    from apps.media.repositories import doc_chunk_repo, media_attachment_repo

    if attachment.parsed_content_path:
        minio_service.delete_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=attachment.parsed_content_path)

    deleted = await doc_chunk_repo.delete_by_attachment_id(attachment.attachment_id)
    if deleted:
        logger.info("Doc cache clear chunks: attachment=%d, deleted=%d", attachment.attachment_id, deleted)

    await media_attachment_repo.clear_parsed_cache(attachment.attachment_id)
    logger.info("Doc cache cleared: attachment=%d", attachment.attachment_id)
