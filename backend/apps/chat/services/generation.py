# 兼容层：真实实现已迁移到 apps.graph.services.generation（source of truth）
# 保留本模块路径以兼容旧导入与字符串 patch 契约。
from apps.graph.services.generation import (  # noqa: F401
    _active_generations,
    get_stop_event,
    map_llm_exception,
    register_generation,
    signal_stop,
    unregister_generation,
)
