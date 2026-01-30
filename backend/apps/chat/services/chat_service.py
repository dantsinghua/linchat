"""
聊天服务 + 历史消息服务

参考:
- behavior-model.md#2.1 发送消息并获取响应（B_CHAT_001）
- behavior-model.md#2.3 加载历史消息（B_CHAT_003）
- behavior-model.md#2.5 继续生成（B_CHAT_005）
"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator, Optional

from django.conf import settings

from apps.chat.agent import get_thread_id
from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.chat.services.generation import get_stop_event, signal_stop
from apps.chat.services.types import MessageVO, StreamChunk
from apps.common.exceptions import EmptyMessageException, MessageTooLongException

logger = logging.getLogger(__name__)


class ChatService:
    """
    聊天服务

    参考: behavior-model.md#2.1 发送消息并获取响应（B_CHAT_001）
    """

    @staticmethod
    async def send_message(
        user_id: int, content: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        发送消息并获取流式响应

        参考: behavior-model.md#2.1
        """
        from apps.chat.services.agent_service import AgentService

        # [R_MSG_002] 空消息拦截
        content = content.strip()
        if not content:
            raise EmptyMessageException("消息内容不能为空")

        # [R_MSG_001] 消息长度限制
        if len(content) > settings.MAX_MESSAGE_LENGTH:
            raise MessageTooLongException(
                f"消息长度不能超过{settings.MAX_MESSAGE_LENGTH}字符"
            )

        # [R_DATA_001] thread_id 包含 user_id 确保数据隔离
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        thread_id = get_thread_id(user_id)

        async for chunk in AgentService.execute(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            user_message=content,
        ):
            yield chunk

    @staticmethod
    async def stop_generation(user_id: int, request_id: str) -> bool:
        """停止生成 — 参考: spec.md US2场景9"""
        success = signal_stop(request_id)
        if success:
            logger.info(f"Stop signal sent for request {request_id}")
        return success

    @staticmethod
    async def resume_generation(
        user_id: int, request_id: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        继续生成（从中断处恢复）

        参考: behavior-model.md#2.5 继续生成（B_CHAT_005）
        """
        from apps.chat.services.agent_service import AgentService

        # 1. 查询被中断的消息
        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return

        if message.status != Message.STATUS_INTERRUPTED:
            yield StreamChunk(type="error", content="该消息不可继续生成")
            return

        thread_id = get_thread_id(user_id)

        # 更新消息状态为生成中
        await message_repo.update_status(message.message_id, user_id, Message.STATUS_GENERATING)

        # 继续生成
        async for chunk in AgentService.resume(
            user_id=user_id, thread_id=thread_id, request_id=request_id, message=message
        ):
            yield chunk

    @staticmethod
    async def reconnect_stream(
        user_id: int, request_id: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        重连流式响应（用于页面刷新时重连生成中的消息）

        参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）
        """
        # 1. 查询生成中的消息
        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return

        if message.status != Message.STATUS_GENERATING:
            # 消息已不在生成中，返回当前状态
            if message.status == Message.STATUS_NORMAL:
                yield StreamChunk(
                    type="done", content="", message_id=message.message_id,
                )
            elif message.status == Message.STATUS_INTERRUPTED:
                yield StreamChunk(
                    type="interrupted", content="[已中断]", message_id=message.message_id,
                )
            elif message.status == Message.STATUS_FAILED:
                yield StreamChunk(type="error", content="生成失败")
            return

        # 2. 检查是否有活跃的生成任务
        stop_event = get_stop_event(request_id)
        if not stop_event:
            # 没有活跃的生成任务，可能服务重启了
            await message_repo.update_status(
                message.message_id, user_id, Message.STATUS_INTERRUPTED
            )
            yield StreamChunk(
                type="interrupted", content="[已中断]", message_id=message.message_id,
            )
            return

        # 3. 发送当前已有的内容
        if message.content:
            yield StreamChunk(
                type="content", content=message.content, message_id=message.message_id,
            )

        # 4. 等待生成完成或中断（轮询数据库状态）
        max_wait = 300  # 最大等待5分钟
        poll_interval = 0.5
        last_content = message.content or ""

        for _ in range(int(max_wait / poll_interval)):
            await asyncio.sleep(poll_interval)

            updated_msg = await message_repo.get_by_request_id(request_id, user_id)
            if not updated_msg:
                yield StreamChunk(type="error", content="消息不存在")
                return

            # 推送新增的内容
            if updated_msg.content and len(updated_msg.content) > len(last_content):
                new_content = updated_msg.content[len(last_content):]
                if new_content.endswith("[已中断]"):
                    new_content = new_content[:-6]
                if new_content:
                    yield StreamChunk(
                        type="content", content=new_content, message_id=updated_msg.message_id,
                    )
                last_content = updated_msg.content.replace("[已中断]", "")

            # 检查是否已完成
            if updated_msg.status == Message.STATUS_NORMAL:
                yield StreamChunk(
                    type="done", content="", message_id=updated_msg.message_id,
                )
                return
            elif updated_msg.status == Message.STATUS_INTERRUPTED:
                yield StreamChunk(
                    type="interrupted", content="[已中断]", message_id=updated_msg.message_id,
                )
                return
            elif updated_msg.status == Message.STATUS_FAILED:
                yield StreamChunk(type="error", content="生成失败")
                return

        # 超时
        yield StreamChunk(type="error", content="重连超时")


class HistoryService:
    """
    历史消息服务

    参考: behavior-model.md#2.3 加载历史消息（B_CHAT_003）
    """

    @staticmethod
    async def load_messages(
        user_id: int, limit: int = 50, before_sequence: Optional[int] = None
    ) -> list[MessageVO]:
        """加载历史消息（按时间正序）[R_DATA_001]"""
        limit = min(limit, 100)

        if before_sequence:
            messages = await message_repo.find_by_user_before_sequence(
                user_id=user_id, before_sequence=before_sequence, limit=limit
            )
        else:
            messages = await message_repo.find_latest_by_user(
                user_id=user_id, limit=limit
            )

        messages.reverse()
        return [MessageVO.from_entity(m) for m in messages]

    @staticmethod
    async def get_generating_message(user_id: int) -> Optional[MessageVO]:
        """获取正在生成中的消息（用于页面刷新时检测）"""
        message = await message_repo.find_generating_message(user_id)
        if message:
            return MessageVO.from_entity(message)
        return None
