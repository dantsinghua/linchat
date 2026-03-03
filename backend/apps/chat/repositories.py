from datetime import timedelta
from typing import Optional
from asgiref.sync import sync_to_async
from django.db.models import Max
from django.utils import timezone
from apps.chat.models import LangGraphExecution, Message
from apps.media.repositories import MediaAttachmentRepository, media_attachment_repo  # noqa: F401


class MessageRepository:

    @staticmethod
    @sync_to_async
    def create(message: Message) -> Message:
        message.save(); return message

    @staticmethod
    @sync_to_async
    def get_by_id(message_id: int, user_id: int) -> Optional[Message]:
        try: return Message.objects.get(message_id=message_id, user_id=user_id)
        except Message.DoesNotExist: return None

    @staticmethod
    @sync_to_async
    def get_by_uuid(message_uuid: str, user_id: int) -> Optional[Message]:
        try: return Message.objects.get(message_uuid=message_uuid, user_id=user_id)
        except Message.DoesNotExist: return None

    @staticmethod
    @sync_to_async
    def get_by_request_id(
        request_id: str, user_id: int, role: Optional[str] = "assistant"
    ) -> Optional[Message]:
        qs = Message.objects.filter(request_id=request_id, user_id=user_id)
        if role is not None:
            qs = qs.filter(role=role)
        return qs.first()

    @staticmethod
    @sync_to_async
    def update(message: Message) -> Message:
        message.save(); return message

    @staticmethod
    @sync_to_async
    def update_status(message_id: int, user_id: int, status: int) -> bool:
        return Message.objects.filter(message_id=message_id, user_id=user_id).update(status=status) > 0

    @staticmethod
    @sync_to_async
    def update_content_and_status(message_id: int, user_id: int, content: str, status: int) -> bool:
        return Message.objects.filter(message_id=message_id, user_id=user_id).update(content=content, status=status) > 0

    @staticmethod
    @sync_to_async
    def update_content(message_id: int, content: str) -> bool:
        return Message.objects.filter(message_id=message_id).update(content=content) > 0

    @staticmethod
    @sync_to_async
    def get_max_sequence(user_id: int) -> int:
        return Message.objects.filter(user_id=user_id).aggregate(max_seq=Max("sequence"))["max_seq"] or 0

    @staticmethod
    @sync_to_async
    def get_next_sequence(user_id: int) -> int:
        return (Message.objects.filter(user_id=user_id).aggregate(max_seq=Max("sequence"))["max_seq"] or 0) + 1

    @staticmethod
    @sync_to_async
    def find_latest_by_user(user_id: int, limit: int = 50) -> list[Message]:
        return list(Message.objects.filter(user_id=user_id).prefetch_related("attachments").order_by("-created_time")[:limit])

    @staticmethod
    @sync_to_async
    def find_by_user_before_sequence(user_id: int, before_sequence: int, limit: int = 50) -> list[Message]:
        return list(Message.objects.filter(user_id=user_id, sequence__lt=before_sequence).prefetch_related("attachments").order_by("-sequence")[:limit])

    @staticmethod
    @sync_to_async
    def search_messages(user_id: int, keyword: str = "", days: int = 30, limit: int = 20) -> list[Message]:
        qs = Message.objects.filter(user_id=user_id, status=1)
        if keyword: qs = qs.filter(content__icontains=keyword)
        if days > 0: qs = qs.filter(created_time__gte=timezone.now() - timedelta(days=days))
        return list(qs.order_by("-created_time")[:limit])

    @staticmethod
    @sync_to_async
    def find_generating_message(user_id: int) -> Optional[Message]:
        return Message.objects.filter(user_id=user_id, role=Message.ROLE_ASSISTANT, status=Message.STATUS_GENERATING).prefetch_related("attachments").first()


class ExecutionRepository:

    @staticmethod
    @sync_to_async
    def create(execution: LangGraphExecution) -> LangGraphExecution:
        execution.save(); return execution

    @staticmethod
    @sync_to_async
    def update(execution: LangGraphExecution) -> LangGraphExecution:
        execution.save(); return execution


message_repo = MessageRepository()
execution_repo = ExecutionRepository()
