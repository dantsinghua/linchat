from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async

from apps.media.models import MediaAttachment


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


media_attachment_repo = MediaAttachmentRepository()
