"""兼容层：类型定义已迁移到 apps.graph.services.types（batch-16）。

原 import 路径 apps.chat.services.types 仍可用，下一轮清理。
"""

from apps.graph.services.types import (  # noqa: F401
    InferenceTask,
    MessageVO,
    StreamChunk,
    _get_tool_model_name,
)
