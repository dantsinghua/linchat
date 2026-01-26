"""
消息仓库层

参考:
- data-model.md#2.2 消息表（message）
- behavior-model.md#2.3 加载历史消息（B_CHAT_003）
- rule-model.md#R_DATA_001 用户数据隔离规则
"""

import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Max

from apps.chat.models import LangGraphExecution, Message

logger = logging.getLogger(__name__)


class MessageRepository:
    """
    消息仓库

    实现消息的 CRUD 操作，确保用户数据隔离
    参考: data-model.md#2.2 消息表
    """

    @staticmethod
    @sync_to_async
    def create(message: Message) -> Message:
        """
        创建消息

        Args:
            message: 消息实体

        Returns:
            Message: 保存后的消息（含主键）
        """
        message.save()
        logger.debug(f"Created message: {message.message_uuid}")
        return message

    @staticmethod
    @sync_to_async
    def get_by_id(message_id: int, user_id: int) -> Optional[Message]:
        """
        根据ID获取消息

        Args:
            message_id: 消息ID
            user_id: 用户ID（数据隔离）

        Returns:
            Optional[Message]: 消息实体或 None

        Note:
            [R_DATA_001] 必须通过 user_id 过滤确保数据隔离
        """
        try:
            return Message.objects.get(message_id=message_id, user_id=user_id)
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_uuid(message_uuid: str, user_id: int) -> Optional[Message]:
        """
        根据UUID获取消息

        Args:
            message_uuid: 消息UUID
            user_id: 用户ID（数据隔离）

        Returns:
            Optional[Message]: 消息实体或 None
        """
        try:
            return Message.objects.get(message_uuid=message_uuid, user_id=user_id)
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_by_request_id(request_id: str, user_id: int) -> Optional[Message]:
        """
        根据 request_id 获取消息（通常是 assistant 消息）

        Args:
            request_id: 请求ID
            user_id: 用户ID（数据隔离）

        Returns:
            Optional[Message]: 消息实体或 None
        """
        try:
            return Message.objects.filter(
                request_id=request_id, user_id=user_id, role=Message.ROLE_ASSISTANT
            ).first()
        except Message.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def update(message: Message) -> Message:
        """
        更新消息

        Args:
            message: 消息实体

        Returns:
            Message: 更新后的消息
        """
        message.save()
        logger.debug(f"Updated message: {message.message_uuid}")
        return message

    @staticmethod
    @sync_to_async
    def update_status(message_id: int, user_id: int, status: int) -> bool:
        """
        更新消息状态

        Args:
            message_id: 消息ID
            user_id: 用户ID（数据隔离）
            status: 新状态

        Returns:
            bool: 是否更新成功
        """
        updated = Message.objects.filter(message_id=message_id, user_id=user_id).update(
            status=status
        )
        return updated > 0

    @staticmethod
    @sync_to_async
    def update_content_and_status(
        message_id: int, user_id: int, content: str, status: int
    ) -> bool:
        """
        更新消息内容和状态（用于中断/恢复场景）

        Args:
            message_id: 消息ID
            user_id: 用户ID（数据隔离）
            content: 新内容
            status: 新状态

        Returns:
            bool: 是否更新成功
        """
        updated = Message.objects.filter(message_id=message_id, user_id=user_id).update(
            content=content, status=status
        )
        return updated > 0

    @staticmethod
    @sync_to_async
    def get_max_sequence(user_id: int) -> int:
        """
        获取用户消息的最大序号

        Args:
            user_id: 用户ID

        Returns:
            int: 最大序号，若无消息则返回 0
        """
        result = Message.objects.filter(user_id=user_id).aggregate(
            max_seq=Max("sequence")
        )
        return result["max_seq"] or 0

    @staticmethod
    @sync_to_async
    def find_latest_by_user(user_id: int, limit: int = 50) -> list[Message]:
        """
        获取用户最新的消息（用于首次加载）

        参考: behavior-model.md#2.3 加载历史消息

        Args:
            user_id: 用户ID
            limit: 返回数量限制

        Returns:
            list[Message]: 消息列表（按 created_time 倒序，调用方需反转）

        Note:
            [R_DATA_001] 通过 user_id 过滤确保数据隔离
            返回倒序是为了获取"最新的N条"，调用方需 reverse() 得到正序
        """
        return list(
            Message.objects.filter(user_id=user_id).order_by("-created_time")[:limit]
        )

    @staticmethod
    @sync_to_async
    def find_by_user_before_sequence(
        user_id: int, before_sequence: int, limit: int = 50
    ) -> list[Message]:
        """
        获取指定序号之前的消息（用于向上滚动加载更多）

        参考: process-model.md#四、历史消息加载流程

        Args:
            user_id: 用户ID
            before_sequence: 游标序号（不包含）
            limit: 返回数量限制

        Returns:
            list[Message]: 消息列表（按 sequence 倒序，调用方需反转）
        """
        return list(
            Message.objects.filter(
                user_id=user_id, sequence__lt=before_sequence
            ).order_by("-sequence")[:limit]
        )

    @staticmethod
    @sync_to_async
    def find_generating_message(user_id: int) -> Optional[Message]:
        """
        查找用户正在生成中的消息（用于页面刷新时重连SSE）

        参考: behavior-model.md#2.4 流式响应重连

        Args:
            user_id: 用户ID

        Returns:
            Optional[Message]: 生成中的消息或 None
        """
        return Message.objects.filter(
            user_id=user_id,
            role=Message.ROLE_ASSISTANT,
            status=Message.STATUS_GENERATING,
        ).first()

    @staticmethod
    @sync_to_async
    def count_by_user(user_id: int) -> int:
        """
        统计用户消息数量

        Args:
            user_id: 用户ID

        Returns:
            int: 消息数量
        """
        return Message.objects.filter(user_id=user_id).count()


class ExecutionRepository:
    """
    LangGraph 执行记录仓库

    参考: data-model.md#2.3 执行监控表（langgraph_execution）
    """

    @staticmethod
    @sync_to_async
    def create(execution: LangGraphExecution) -> LangGraphExecution:
        """
        创建执行记录

        Args:
            execution: 执行记录实体

        Returns:
            LangGraphExecution: 保存后的记录
        """
        execution.save()
        logger.debug(f"Created execution: {execution.execution_uuid}")
        return execution

    @staticmethod
    @sync_to_async
    def update(execution: LangGraphExecution) -> LangGraphExecution:
        """
        更新执行记录

        Args:
            execution: 执行记录实体

        Returns:
            LangGraphExecution: 更新后的记录
        """
        execution.save()
        return execution

    @staticmethod
    @sync_to_async
    def get_by_request_id(request_id: str) -> Optional[LangGraphExecution]:
        """
        根据 request_id 获取执行记录

        Args:
            request_id: 请求ID

        Returns:
            Optional[LangGraphExecution]: 执行记录或 None
        """
        try:
            return LangGraphExecution.objects.get(request_id=request_id)
        except LangGraphExecution.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def update_status(
        request_id: str,
        status: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        更新执行状态

        Args:
            request_id: 请求ID
            status: 新状态
            error_type: 错误类型（可选）
            error_message: 错误信息（可选）

        Returns:
            bool: 是否更新成功
        """
        update_fields = {"status": status}
        if error_type:
            update_fields["error_type"] = error_type
        if error_message:
            update_fields["error_message"] = error_message

        updated = LangGraphExecution.objects.filter(request_id=request_id).update(
            **update_fields
        )
        return updated > 0


# 单例实例
message_repo = MessageRepository()
execution_repo = ExecutionRepository()
