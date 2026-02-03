"""
聊天服务数据类型

包含 StreamChunk、MessageVO 等数据类定义。
"""

from dataclasses import dataclass
from typing import Optional

from asgiref.sync import sync_to_async

from apps.chat.models import Message
from apps.models.services import model_service


async def _get_language_model_name() -> str:
    """从数据库获取激活的语言模型名称"""
    config = await sync_to_async(model_service.get_active_model)("language")
    return config["name"] if config else "unknown"


@dataclass
class StreamChunk:
    """流式响应块"""

    type: str  # content, done, error, interrupted
    content: str
    message_id: Optional[int] = None
    request_id: Optional[str] = None  # 首个 chunk 返回，用于前端停止/继续生成


@dataclass
class MessageVO:
    """消息视图对象"""

    message_id: int
    message_uuid: str
    role: str
    content: str
    status: int
    sequence: int
    created_time: str
    request_id: Optional[str] = None
    model_name: Optional[str] = None
    response_time_ms: Optional[int] = None

    @classmethod
    def from_entity(cls, message: Message) -> "MessageVO":
        """从实体转换"""
        return cls(
            message_id=message.message_id,
            message_uuid=message.message_uuid,
            role=message.role,
            content=message.content,
            status=message.status,
            sequence=message.sequence,
            created_time=message.created_time.isoformat(),
            request_id=message.request_id,
            model_name=message.model_name,
            response_time_ms=message.response_time_ms,
        )
