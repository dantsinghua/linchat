"""
聊天服务包

所有公共 API 在此重新导出，兼容现有 import 路径：
    from apps.chat.services import ChatService, AgentService, ...
"""

from apps.chat.services.agent_service import AgentService
from apps.chat.services.chat_service import ChatService, HistoryService
from apps.chat.services.generation import (
    _active_generations,
    get_stop_event,
    map_llm_exception,
    register_generation,
    signal_stop,
    unregister_generation,
)
from apps.chat.services.types import MessageVO, StreamChunk

__all__ = [
    "ChatService",
    "HistoryService",
    "AgentService",
    "StreamChunk",
    "MessageVO",
    "register_generation",
    "unregister_generation",
    "get_stop_event",
    "signal_stop",
    "map_llm_exception",
    "_active_generations",
]
