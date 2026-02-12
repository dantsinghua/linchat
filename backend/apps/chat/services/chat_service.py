"""聊天服务 + 历史消息服务

参考:
- behavior-model.md#2.1~2.5
"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator, Optional

from django.conf import settings

from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.chat.services.generation import get_stop_event, signal_stop
from apps.chat.services.types import MessageVO, StreamChunk
from apps.common.exceptions import EmptyMessageException, MessageTooLongException
from apps.graph.agent import get_thread_id

logger = logging.getLogger(__name__)


def _status_chunk(message: Message) -> Optional[StreamChunk]:
    """根据消息状态返回对应的 StreamChunk，或 None"""
    if message.status == Message.STATUS_NORMAL:
        return StreamChunk(type="done", content="", message_id=message.message_id)
    if message.status == Message.STATUS_INTERRUPTED:
        return StreamChunk(type="interrupted", content="[已中断]", message_id=message.message_id)
    if message.status == Message.STATUS_FAILED:
        return StreamChunk(type="error", content="生成失败")
    return None


class ChatService:

    @staticmethod
    async def send_message(
        user_id: int,
        content: str,
        attachment_uuids: Optional[list[str]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """发送消息（支持多模态附件）

        Args:
            user_id: 用户 ID
            content: 消息内容
            attachment_uuids: 附件 UUID 列表

        Yields:
            StreamChunk: 流式响应块
        """
        from apps.graph.services import AgentService

        content = content.strip()
        if not content:
            raise EmptyMessageException("消息内容不能为空")
        if len(content) > settings.MAX_MESSAGE_LENGTH:
            raise MessageTooLongException(f"消息长度不能超过{settings.MAX_MESSAGE_LENGTH}字符")

        request_id = f"req_{uuid.uuid4().hex[:16]}"
        thread_id = get_thread_id(user_id)

        async for chunk in AgentService.execute(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            user_message=content,
            attachment_uuids=attachment_uuids,
        ):
            yield chunk

    @staticmethod
    async def stop_generation(user_id: int, request_id: str) -> bool:
        success = signal_stop(request_id)
        if success:
            logger.info(f"Stop signal sent for request {request_id}")
        return success

    @staticmethod
    async def resume_generation(user_id: int, request_id: str) -> AsyncGenerator[StreamChunk, None]:
        from apps.graph.services import AgentService

        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return
        if message.status != Message.STATUS_INTERRUPTED:
            yield StreamChunk(type="error", content="该消息不可继续生成")
            return

        await message_repo.update_status(message.message_id, user_id, Message.STATUS_GENERATING)

        async for chunk in AgentService.resume(
            user_id=user_id, thread_id=get_thread_id(user_id),
            request_id=request_id, message=message,
        ):
            yield chunk

    @staticmethod
    async def reconnect_stream(user_id: int, request_id: str) -> AsyncGenerator[StreamChunk, None]:
        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return

        # 非生成中状态直接返回结果
        if message.status != Message.STATUS_GENERATING:
            chunk = _status_chunk(message)
            if chunk:
                yield chunk
            return

        # 无活跃生成任务则标记中断
        stop_event = get_stop_event(request_id)
        if not stop_event:
            await message_repo.update_status(message.message_id, user_id, Message.STATUS_INTERRUPTED)
            yield StreamChunk(type="interrupted", content="[已中断]", message_id=message.message_id)
            return

        # 发送已有内容
        if message.content:
            yield StreamChunk(type="content", content=message.content, message_id=message.message_id)

        # 轮询等待完成
        last_content = message.content or ""
        for _ in range(600):  # 5 min @ 0.5s
            await asyncio.sleep(0.5)

            updated = await message_repo.get_by_request_id(request_id, user_id)
            if not updated:
                yield StreamChunk(type="error", content="消息不存在")
                return

            # 推送增量内容
            if updated.content and len(updated.content) > len(last_content):
                new_content = updated.content[len(last_content):]
                if new_content.endswith("[已中断]"):
                    new_content = new_content[:-6]
                if new_content:
                    yield StreamChunk(type="content", content=new_content, message_id=updated.message_id)
                last_content = updated.content.replace("[已中断]", "")

            chunk = _status_chunk(updated)
            if chunk:
                yield chunk
                return

        yield StreamChunk(type="error", content="重连超时")


class HistoryService:

    @staticmethod
    async def load_messages(
        user_id: int, limit: int = 50, before_sequence: Optional[int] = None
    ) -> list[MessageVO]:
        limit = min(limit, 100)
        if before_sequence:
            messages = await message_repo.find_by_user_before_sequence(
                user_id=user_id, before_sequence=before_sequence, limit=limit,
            )
        else:
            messages = await message_repo.find_latest_by_user(user_id=user_id, limit=limit)
        messages.reverse()
        return [MessageVO.from_entity(m) for m in messages]

    @staticmethod
    async def get_generating_message(user_id: int) -> Optional[MessageVO]:
        message = await message_repo.find_generating_message(user_id)
        return MessageVO.from_entity(message) if message else None
