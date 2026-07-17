"""
聊天服务包

兼容层：部分服务已迁移到 apps.graph / apps.media / apps.common 模块。
所有原始 import 路径仍可用。
"""

from apps.chat.services.chat_service import ChatService, HistoryService
from apps.chat.services.generation import (
    _active_generations,
    get_stop_event,
    map_llm_exception,
    register_generation,
    signal_stop,
    unregister_generation,
)
from apps.chat.services.types import InferenceTask, MessageVO, StreamChunk, _get_tool_model_name

# 兼容层导出（已迁移到其他模块）
from apps.graph.services.context_service import ContextService  # noqa: F401
from apps.chat.services.document_parse_service import DocumentParseError, DocumentParseService, document_parse_service  # noqa: F401
from apps.chat.services.inference_service import InferenceService, inference_service  # noqa: F401
from apps.chat.services.media_service import MediaService, MediaUploadError, media_service  # noqa: F401
from apps.chat.services.minio_service import MinioService, minio_service  # noqa: F401

__all__ = [
    "ChatService", "HistoryService",
    "StreamChunk", "MessageVO", "InferenceTask",
    "register_generation", "unregister_generation", "get_stop_event", "signal_stop",
    "map_llm_exception", "_active_generations", "_get_tool_model_name",
    # 兼容层
    "ContextService", "MinioService", "minio_service",
    "InferenceService", "inference_service",
    "MediaService", "MediaUploadError", "media_service",
    "DocumentParseService", "DocumentParseError", "document_parse_service",
]
