"""
SSE 事件推送服务

参考:
- process-model.md#一点五、单点登录SSE推送流程
- tasks.md#T015c

事件类型:
- logout: 登出事件，含 reason 字段（SSO_CONFLICT 表示其他设备登录）
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator

from django.http import StreamingHttpResponse

from core.redis import get_redis, get_user_events_channel

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """SSE 事件类型"""

    LOGOUT = "logout"
    MESSAGE = "message"
    HEARTBEAT = "heartbeat"


class LogoutReason(str, Enum):
    """登出原因"""

    SSO_CONFLICT = "SSO_CONFLICT"  # 其他设备登录
    TOKEN_EXPIRED = "TOKEN_EXPIRED"  # Token 过期
    ADMIN_KICK = "ADMIN_KICK"  # 管理员踢出


@dataclass
class SSEEvent:
    """SSE 事件数据结构"""

    event_type: EventType
    data: dict[str, Any]
    event_id: str | None = None

    def to_sse_format(self) -> str:
        """转换为 SSE 格式字符串"""
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        lines.append(f"event: {self.event_type.value}")
        lines.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        lines.append("")  # SSE 消息以空行结尾
        return "\n".join(lines) + "\n"


class EventService:
    """事件推送服务"""

    @staticmethod
    async def publish_logout_event(user_id: int, reason: LogoutReason) -> bool:
        """
        发布登出事件

        用于单点登录时通知旧会话退出

        Args:
            user_id: 用户ID
            reason: 登出原因

        Returns:
            是否发布成功
        """
        try:
            client = await get_redis()
            channel = get_user_events_channel(user_id)

            event = SSEEvent(
                event_type=EventType.LOGOUT,
                data={
                    "type": EventType.LOGOUT.value,
                    "reason": reason.value,
                    "message": EventService._get_logout_message(reason),
                },
            )

            await client.publish(channel, event.to_sse_format())
            logger.info(f"Published logout event for user {user_id}, reason: {reason.value}")
            return True

        except Exception as e:
            logger.error(f"Failed to publish logout event for user {user_id}: {e}")
            return False

    @staticmethod
    def _get_logout_message(reason: LogoutReason) -> str:
        """获取登出消息"""
        messages = {
            LogoutReason.SSO_CONFLICT: "您已在其他设备登录",
            LogoutReason.TOKEN_EXPIRED: "登录已过期",
            LogoutReason.ADMIN_KICK: "您已被管理员踢出",
        }
        return messages.get(reason, "请重新登录")

    @staticmethod
    async def subscribe_user_events(user_id: int) -> AsyncGenerator[str, None]:
        """
        订阅用户事件

        用于 SSE 端点持续推送事件

        Args:
            user_id: 用户ID

        Yields:
            SSE 格式的事件字符串
        """
        client = await get_redis()
        pubsub = client.pubsub()
        channel = get_user_events_channel(user_id)

        try:
            await pubsub.subscribe(channel)
            logger.info(f"User {user_id} subscribed to events channel")

            # 发送连接成功事件
            yield SSEEvent(
                event_type=EventType.MESSAGE,
                data={"type": "connected", "message": "事件连接已建立"},
            ).to_sse_format()

            # 心跳间隔（秒）
            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                # 检查是否需要发送心跳
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield SSEEvent(
                        event_type=EventType.HEARTBEAT,
                        data={"type": "heartbeat"},
                    ).to_sse_format()
                    last_heartbeat = current_time

                # 等待消息（带超时）
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        yield message["data"]
                except asyncio.TimeoutError:
                    continue

        except asyncio.CancelledError:
            logger.info(f"User {user_id} event subscription cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in event subscription for user {user_id}: {e}")
            raise
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


def create_sse_response(generator: AsyncGenerator[str, None]) -> StreamingHttpResponse:
    """
    创建 SSE 流式响应

    Args:
        generator: 异步事件生成器

    Returns:
        StreamingHttpResponse
    """

    def sync_generator():
        """将异步生成器转换为同步生成器"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agen = generator.__aiter__()
            while True:
                try:
                    yield loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    response = StreamingHttpResponse(
        sync_generator(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # 禁用 nginx 缓冲
    return response
