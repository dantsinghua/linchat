"""公共组件模块 — 基础设施工具集合。"""
from __future__ import annotations

import contextvars

# trace_id 全局上下文变量（batch-04）
# 由 core.middleware.TraceIdMiddleware 在请求进入时 set；
# 由 core.logging_config.TraceIdFilter 读取注入到每条日志。
# 其他模块（celery / voice consumer / langgraph）如需主动覆盖，
# 请 trace_id_var.set(...) 并保留 Token 用于 .reset()。
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """读取当前上下文 trace_id；无则返回空串。"""
    return trace_id_var.get() or ""


__all__ = ["trace_id_var", "get_trace_id"]
