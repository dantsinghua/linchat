"""SSE 事件推送服务"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator

from core.redis import get_redis, get_user_events_channel

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    LOGOUT = "logout"
    MESSAGE = "message"
    HEARTBEAT = "heartbeat"
    CONTEXT_STATUS = "context_status"
    INFERENCE_CANCEL = "inference_cancel"  # 推理取消事件
    DOC_PARSE_PROGRESS = "doc_parse_progress"  # 文档解析进度事件


class LogoutReason(str, Enum):
    SSO_CONFLICT = "SSO_CONFLICT"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    ADMIN_KICK = "ADMIN_KICK"


_LOGOUT_MESSAGES = {
    LogoutReason.SSO_CONFLICT: "您已在其他设备登录",
    LogoutReason.TOKEN_EXPIRED: "登录已过期",
    LogoutReason.ADMIN_KICK: "您已被管理员踢出",
}


@dataclass
class SSEEvent:
    """SSE 事件"""

    event_type: EventType
    data: dict[str, Any]
    event_id: str | None = None

    def to_sse_format(self) -> str:
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        lines.append(f"event: {self.event_type.value}")
        lines.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        lines.append("")
        return "\n".join(lines) + "\n"


def build_doc_parse_event(task_id: str, status: str, progress: dict, file_name: str,
                          suggestion: str = None, error_message: str = None) -> dict:
    evt = EventType.DOC_PARSE_PROGRESS.value
    return {"type": evt, "task_id": task_id, "status": status, "progress": progress,
            "file_name": file_name, "suggestion": suggestion, "error_message": error_message}


class EventService:

    @staticmethod
    async def publish_logout_event(user_id: int, reason: LogoutReason) -> bool:
        try:
            client = await get_redis()
            channel = get_user_events_channel(user_id)
            event = SSEEvent(
                event_type=EventType.LOGOUT,
                data={
                    "type": EventType.LOGOUT.value,
                    "reason": reason.value,
                    "message": _LOGOUT_MESSAGES.get(reason, "请重新登录"),
                },
            )
            await client.publish(channel, event.to_sse_format())
            logger.info(f"Published logout event for user {user_id}, reason: {reason.value}")
            return True
        except Exception as e:
            logger.error(f"Failed to publish logout event for user {user_id}: {e}")
            return False

    @staticmethod
    async def publish_event(user_id: int, event_type: str, data: dict[str, Any]) -> bool:
        """发布通用事件到用户频道

        Args:
            user_id: 目标用户 ID
            event_type: 事件类型标识
            data: 事件数据负载
        """
        try:
            client = await get_redis()
            channel = get_user_events_channel(user_id)
            event = SSEEvent(
                event_type=EventType(event_type),
                data=data,
            )
            await client.publish(channel, event.to_sse_format())
            return True
        except Exception as e:
            logger.warning("Failed to publish %s event for user %d: %s", event_type, user_id, e)
            return False

    @staticmethod
    async def subscribe_user_events(user_id: int) -> AsyncGenerator[str, None]:
        client = await get_redis()
        pubsub = client.pubsub()
        channel = get_user_events_channel(user_id)

        try:
            await pubsub.subscribe(channel)
            logger.info(f"User {user_id} subscribed to events channel")

            yield SSEEvent(
                event_type=EventType.MESSAGE,
                data={"type": "connected", "message": "事件连接已建立"},
            ).to_sse_format()

            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield SSEEvent(
                        event_type=EventType.HEARTBEAT,
                        data={"type": "heartbeat"},
                    ).to_sse_format()
                    last_heartbeat = current_time

                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        yield message["data"]
                    else:
                        await asyncio.sleep(0.5)
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
