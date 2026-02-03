"""
消息仓库层

参考:
- data-model.md#2.2 消息表（message）
- behavior-model.md#2.3 加载历史消息（B_CHAT_003）
- rule-model.md#R_DATA_001 用户数据隔离规则
"""

from typing import Optional

from asgiref.sync import sync_to_async
from django.db.models import Max

from apps.chat.models import LangGraphExecution, Message


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
        """
        return list(
            Message.objects.filter(user_id=user_id).order_by("-created_time")[:limit]
        )

    @staticmethod
    @sync_to_async
    def find_by_user_before_sequence(
        user_id: int, before_sequence: int, limit: int = 50
    ) -> list[Message]:
        """获取指定序号之前的消息（用于向上滚动加载更多），返回倒序"""
        return list(
            Message.objects.filter(
                user_id=user_id, sequence__lt=before_sequence
            ).order_by("-sequence")[:limit]
        )

    @staticmethod
    @sync_to_async
    def find_generating_message(user_id: int) -> Optional[Message]:
        """查找用户正在生成中的消息（用于页面刷新时重连SSE）"""
        return Message.objects.filter(
            user_id=user_id,
            role=Message.ROLE_ASSISTANT,
            status=Message.STATUS_GENERATING,
        ).first()


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


# 单例实例
message_repo = MessageRepository()
execution_repo = ExecutionRepository()
