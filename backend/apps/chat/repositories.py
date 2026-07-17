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

    # --- batch-33: voice service 分层收敛（1 sync get / 1 sync flag 供 transaction.atomic 同步块；3 async 独立操作）---

    @staticmethod
    def get_by_request_id_sync(request_id: str, user_id: int, role: Optional[str] = "assistant") -> Optional[Message]:
        """同步版 get_by_request_id：供 transaction.atomic() 同步块内调用，与调用方共享同一事务/线程。"""
        qs = Message.objects.filter(request_id=request_id, user_id=user_id)
        if role is not None:
            qs = qs.filter(role=role)
        return qs.first()

    @staticmethod
    def set_voice_flag_sync(message: Message) -> None:
        """同步标记 is_voice=True：供 transaction.atomic() 同步块内调用。"""
        message.is_voice = True
        message.save(update_fields=["is_voice"])

    @staticmethod
    @sync_to_async
    def delete_excess_record_only(user_id: int, limit: int) -> int:
        """ambient record-only 超限清理：排除已回复请求后，删除最旧的超限记录。返回删除条数。"""
        from django.db.models import Subquery
        replied_ids = Message.objects.filter(user_id=user_id, role="assistant", is_voice=True).values("request_id")
        record_only_qs = Message.objects.filter(
            user_id=user_id, role="user", is_voice=True
        ).exclude(request_id__in=Subquery(replied_ids))
        count = record_only_qs.count()
        if count <= limit:
            return 0
        excess = count - limit
        oldest_ids = list(record_only_qs.order_by("created_time").values_list("message_id", flat=True)[:excess])
        if oldest_ids:
            Message.objects.filter(message_id__in=oldest_ids).delete()
        return excess

    @staticmethod
    @sync_to_async
    def reassign_speaker_messages(old_label: str, user_id: int) -> int:
        """声纹追溯匹配：把未知 speaker 标签的语音消息改归属到 user_id。返回更新条数。"""
        return Message.objects.filter(speaker_id=old_label, is_voice=True).update(
            speaker_id=str(user_id), user_id=user_id)

    @staticmethod
    @sync_to_async
    def update_content_by_request_id(request_id: str, user_id: int, content: str, role: str = "user") -> int:
        """按 request_id 更新消息内容（ambient 用户消息改为 ASR 原文）。返回更新条数。"""
        return Message.objects.filter(request_id=request_id, user_id=user_id, role=role).update(content=content)


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
