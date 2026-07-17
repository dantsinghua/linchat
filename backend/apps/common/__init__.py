"""公共组件模块 — 基础设施工具集合。"""
from __future__ import annotations

import contextvars
import uuid

# trace_id 全局上下文变量（batch-04）
# 由 core.middleware.TraceIdMiddleware 在请求进入时 set；
# 由 core.logging_config.TraceIdFilter 读取注入到每条日志。
# 其他模块（celery / voice consumer / langgraph）如需主动覆盖，
# 请 trace_id_var.set(...) 并保留 Token 用于 .reset()。
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """读取当前上下文 trace_id；无则返回空串。"""
    return trace_id_var.get() or ""


def ensure_trace_id() -> str:
    """读取当前 trace_id；为空则生成 32 字符 UUID hex 并 set，返回最终值。

    生成规则与 middleware / chat_service / voice_pipeline 一致（uuid4().hex）。
    """
    tid = trace_id_var.get()
    if not tid:
        tid = uuid.uuid4().hex
        trace_id_var.set(tid)
    return tid


__all__ = ["trace_id_var", "get_trace_id", "ensure_trace_id"]
