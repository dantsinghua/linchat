"""
聊天服务数据类型

包含 StreamChunk、MessageVO、InferenceTask 等数据类定义。

参考:
- specs/008-multimodal-minicpm/data-model.md#2.3 InferenceTask
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.chat.models import MediaAttachment, Message
from apps.models.services import model_service

logger = logging.getLogger(__name__)


async def _get_tool_model_name() -> str:
    """从数据库获取激活的工具模型名称"""
    config = await sync_to_async(model_service.get_active_model)("tool")
    return config["name"] if config else "unknown"


@dataclass
class StreamChunk:
    """流式响应块"""

    type: str  # content, done, error, interrupted
    content: str
    message_id: Optional[int] = None
    request_id: Optional[str] = None  # 首个 chunk 返回，用于前端停止/继续生成
    data: Optional[dict] = None  # 附加数据（如 Gateway 错误信息 retry_after）


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
    attachments: list = field(default_factory=list)

    @classmethod
    def from_entity(cls, message: Message) -> "MessageVO":
        """从实体转换

        注意: 调用方应通过 prefetch_related("attachments") 预加载附件，
        否则每条消息会产生额外的 N+1 查询。
        """
        # 构建附件列表（依赖 prefetch_related 预加载）
        attachment_list = []
        try:
            for att in message.attachments.all():
                attachment_list.append(
                    {
                        "attachment_uuid": att.attachment_uuid,
                        "media_type": att.media_type,
                        "mime_type": att.mime_type,
                        "file_name": att.file_name,
                        "file_size": att.file_size,
                        "width": att.width,
                        "height": att.height,
                        "duration_seconds": att.duration_seconds,
                        "is_expired": att.is_expired,
                        "expires_at": att.expires_at.isoformat() if att.expires_at else None,
                    }
                )
        except Exception as e:
            logger.warning("加载消息附件失败 (message_id=%s): %s", message.message_id, e)

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
            attachments=attachment_list,
        )


@dataclass
class InferenceTask:
    """推理任务状态（Redis 临时存储）

    参考: specs/008-multimodal-minicpm/data-model.md#2.3 InferenceTask
    用于并发控制和中断机制。
    """

    request_id: str
    model: str
    started_at: datetime
    media_types: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(
            {
                "request_id": self.request_id,
                "model": self.model,
                "started_at": self.started_at.isoformat(),
                "media_types": self.media_types,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "InferenceTask":
        """从 JSON 字符串反序列化"""
        d = json.loads(data)
        return cls(
            request_id=d["request_id"],
            model=d["model"],
            started_at=datetime.fromisoformat(d["started_at"]),
            media_types=d.get("media_types", []),
        )

    def elapsed_seconds(self) -> float:
        """计算已运行时长（秒）"""
        now = timezone.now()
        # 确保 started_at 有时区信息
        if timezone.is_naive(self.started_at):
            started = timezone.make_aware(self.started_at)
        else:
            started = self.started_at
        return (now - started).total_seconds()
