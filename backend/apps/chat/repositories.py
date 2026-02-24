"""
消息仓库层

参考:
- data-model.md#2.2 消息表（message）
- behavior-model.md#2.3 加载历史消息（B_CHAT_003）
- rule-model.md#R_DATA_001 用户数据隔离规则
- specs/008-multimodal-minicpm/data-model.md#2.1 MediaAttachment
"""

from datetime import datetime, timedelta
from typing import Optional

from asgiref.sync import sync_to_async
from django.db.models import Max
from django.utils import timezone

from apps.chat.models import LangGraphExecution, MediaAttachment, Message


class MessageRepository:
    """消息仓库 — 所有查询必须包含 user_id 过滤 [R_DATA_001]"""

    @staticmethod
    @sync_to_async
    def create(message: Message) -> Message:
        """创建消息"""
        message.save()
        return message

    @staticmethod
    @sync_to_async
    def get_by_id(message_id: int, user_id: int) -> Optional[Message]:
        """根据ID获取消息 [R_DATA_001]"""
        try:
            return Message.objects.get(message_id=message_id, user_id=user_id)
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuid(message_uuid: str, user_id: int) -> Optional[Message]:
        """根据 UUID 获取消息（含所有权校验）[R_DATA_001]"""
        try:
            return Message.objects.get(message_uuid=message_uuid, user_id=user_id)
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_request_id(request_id: str, user_id: int) -> Optional[Message]:
        """根据 request_id 获取 assistant 消息"""
        try:
            return Message.objects.filter(
                request_id=request_id, user_id=user_id, role=Message.ROLE_ASSISTANT
            ).first()
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def update(message: Message) -> Message:
        """更新消息"""
        message.save()
        return message

    @staticmethod
    @sync_to_async
    def update_status(message_id: int, user_id: int, status: int) -> bool:
        """更新消息状态 [R_DATA_001]"""
        updated = Message.objects.filter(message_id=message_id, user_id=user_id).update(
            status=status
        )
        return updated > 0

    @staticmethod
    @sync_to_async
    def update_content_and_status(
        message_id: int, user_id: int, content: str, status: int
    ) -> bool:
        """更新消息内容和状态（用于中断/恢复场景）"""
        updated = Message.objects.filter(message_id=message_id, user_id=user_id).update(
            content=content, status=status
        )
        return updated > 0

    @staticmethod
    @sync_to_async
    def update_content(message_id: int, content: str) -> bool:
        """仅更新消息内容（用于语音 STT 转写回填，不改变 status）"""
        updated = Message.objects.filter(message_id=message_id).update(
            content=content
        )
        return updated > 0

    @staticmethod
    @sync_to_async
    def get_max_sequence(user_id: int) -> int:
        """获取用户消息的最大序号，若无消息则返回 0"""
        result = Message.objects.filter(user_id=user_id).aggregate(
            max_seq=Max("sequence")
        )
        return result["max_seq"] or 0

    @staticmethod
    @sync_to_async
    def find_latest_by_user(user_id: int, limit: int = 50) -> list[Message]:
        """
        获取用户最新的消息（用于首次加载）

        返回倒序是为了获取"最新的N条"，调用方需 reverse() 得到正序
        [R_DATA_001] 通过 user_id 过滤确保数据隔离
        使用 prefetch_related("attachments") 避免 N+1 查询
        """
        return list(
            Message.objects.filter(user_id=user_id)
            .prefetch_related("attachments")
            .order_by("-created_time")[:limit]
        )

    @staticmethod
    @sync_to_async
    def find_by_user_before_sequence(
        user_id: int, before_sequence: int, limit: int = 50
    ) -> list[Message]:
        """获取指定序号之前的消息（用于向上滚动加载更多），返回倒序

        使用 prefetch_related("attachments") 避免 N+1 查询
        """
        return list(
            Message.objects.filter(
                user_id=user_id, sequence__lt=before_sequence
            )
            .prefetch_related("attachments")
            .order_by("-sequence")[:limit]
        )

    @staticmethod
    @sync_to_async
    def search_messages(
        user_id: int,
        keyword: str = "",
        days: int = 30,
        limit: int = 20,
    ) -> list[Message]:
        """搜索用户历史消息 [R_DATA_001]

        Args:
            user_id: 用户 ID
            keyword: 搜索关键词（模糊匹配 content）
            days: 时间范围（天数），0 表示不限
            limit: 返回数量上限
        """
        qs = Message.objects.filter(user_id=user_id, status=1)
        if keyword:
            qs = qs.filter(content__icontains=keyword)
        if days > 0:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(created_time__gte=cutoff)
        return list(qs.order_by("-created_time")[:limit])

    @staticmethod
    @sync_to_async
    def find_generating_message(user_id: int) -> Optional[Message]:
        """查找用户正在生成中的消息（用于页面刷新时重连SSE）"""
        return (
            Message.objects.filter(
                user_id=user_id,
                role=Message.ROLE_ASSISTANT,
                status=Message.STATUS_GENERATING,
            )
            .prefetch_related("attachments")
            .first()
        )


class ExecutionRepository:
    """LangGraph 执行记录仓库"""

    @staticmethod
    @sync_to_async
    def create(execution: LangGraphExecution) -> LangGraphExecution:
        """创建执行记录"""
        execution.save()
        return execution

    @staticmethod
    @sync_to_async
    def update(execution: LangGraphExecution) -> LangGraphExecution:
        """更新执行记录"""
        execution.save()
        return execution


class MediaAttachmentRepository:
    """媒体附件仓库 — 所有查询必须包含 user_id 过滤 [R_DATA_001]

    参考: specs/008-multimodal-minicpm/data-model.md#2.1 MediaAttachment
    """

    @staticmethod
    @sync_to_async
    def create(attachment: MediaAttachment) -> MediaAttachment:
        """创建媒体附件"""
        attachment.save()
        return attachment

    @staticmethod
    @sync_to_async
    def get_by_uuid(attachment_uuid: str, user_id: int) -> Optional[MediaAttachment]:
        """根据 UUID 获取附件（含所有权校验）[R_DATA_001]"""
        try:
            return MediaAttachment.objects.get(
                attachment_uuid=attachment_uuid, user_id=user_id
            )
        except MediaAttachment.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuid_any_user(attachment_uuid: str) -> Optional[MediaAttachment]:
        """根据 UUID 获取附件（不校验所有权，仅用于内部检查）"""
        try:
            return MediaAttachment.objects.get(attachment_uuid=attachment_uuid)
        except MediaAttachment.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuids(attachment_uuids: list[str], user_id: int) -> list[MediaAttachment]:
        """批量获取附件（含所有权校验）[R_DATA_001]"""
        return list(
            MediaAttachment.objects.filter(
                attachment_uuid__in=attachment_uuids, user_id=user_id
            )
        )

    @staticmethod
    @sync_to_async
    def update(attachment: MediaAttachment) -> MediaAttachment:
        """更新媒体附件"""
        attachment.save()
        return attachment

    @staticmethod
    @sync_to_async
    def associate_message(
        attachment_ids: list[int], message_id: int, user_id: int
    ) -> int:
        """关联附件到消息 [R_DATA_001]"""
        return MediaAttachment.objects.filter(
            attachment_id__in=attachment_ids, user_id=user_id
        ).update(message_id=message_id)

    @staticmethod
    @sync_to_async
    def find_expired(before_date: datetime, limit: int = 100) -> list[MediaAttachment]:
        """查找已过期但未标记的附件"""
        return list(
            MediaAttachment.objects.filter(
                expires_at__lt=before_date, is_expired=False
            )[:limit]
        )

    @staticmethod
    @sync_to_async
    def mark_expired(attachment_ids: list[int]) -> int:
        """批量标记附件为已过期"""
        return MediaAttachment.objects.filter(attachment_id__in=attachment_ids).update(
            is_expired=True
        )


# 单例实例
message_repo = MessageRepository()
execution_repo = ExecutionRepository()
media_attachment_repo = MediaAttachmentRepository()
