"""Trace ID 贯穿中间件 — batch-04 可观测性基础设施。"""
from __future__ import annotations

import uuid
from typing import Callable

from asgiref.sync import iscoroutinefunction
from django.http import HttpRequest, HttpResponse

from apps.common import trace_id_var

TRACE_HEADER = "HTTP_X_REQUEST_ID"
RESP_HEADER = "X-Request-ID"


class TraceIdMiddleware:
    """为每个 HTTP 请求分配 / 继承 trace_id。MIDDLEWARE 顶端注册。"""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._is_async = iscoroutinefunction(get_response)

    def _extract_or_generate(self, request: HttpRequest) -> str:
        incoming = request.META.get(TRACE_HEADER, "").strip()
        if incoming and len(incoming) <= 128:
            return incoming
        return uuid.uuid4().hex  # 与 chat_service:37 / voice_pipeline:64 一致

    def __call__(self, request: HttpRequest):
        if self._is_async:
            return self._acall(request)
        return self._scall(request)

    def _scall(self, request: HttpRequest) -> HttpResponse:
        # 不在 finally 里 reset：uvicorn.access 与 django.request 的 "log_response"
        # 都在本 middleware 返回之后才写日志（前者在 h11 protocol run_asgi、
        # 后者在 BaseHandler.get_response_async 的 >400 分支）。
        # 一旦 reset，两者读到的 trace_id 永远是 "-"。
        # 依赖 asyncio.Task / sync_to_async 的 contextvars 天然隔离即可。
        tid = self._extract_or_generate(request)
        trace_id_var.set(tid)
        request.trace_id = tid
        response = self.get_response(request)
        response[RESP_HEADER] = tid
        return response

    async def _acall(self, request: HttpRequest) -> HttpResponse:
        tid = self._extract_or_generate(request)
        trace_id_var.set(tid)
        request.trace_id = tid
        response = await self.get_response(request)
        response[RESP_HEADER] = tid
        return response
