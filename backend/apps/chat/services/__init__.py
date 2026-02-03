"""
聊天服务包

所有公共 API 在此重新导出，兼容现有 import 路径：
    from apps.chat.services import ChatService, HistoryService, ...

注意: AgentService 已迁移到 apps.graph.services，请从该模块导入。
"""

from apps.chat.services.chat_service import ChatService, HistoryService
from apps.chat.services.context_service import ContextService
from apps.chat.services.generation import (_active_generations, get_stop_event,
                                           map_llm_exception,
                                           register_generation, signal_stop,
                                           unregister_generation)
from apps.chat.services.types import MessageVO, StreamChunk, _get_language_model_name

__all__ = [
    "ChatService",
    "HistoryService",
    "StreamChunk",
    "MessageVO",
    "register_generation",
    "unregister_generation",
    "get_stop_event",
    "signal_stop",
    "map_llm_exception",
    "_active_generations",
    "ContextService",
    "_get_language_model_name",
]
