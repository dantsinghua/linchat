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
        tid = self._extract_or_generate(request)
        token = trace_id_var.set(tid)
        try:
            request.trace_id = tid
            response = self.get_response(request)
            response[RESP_HEADER] = tid
            return response
        finally:
            trace_id_var.reset(token)

    async def _acall(self, request: HttpRequest) -> HttpResponse:
        tid = self._extract_or_generate(request)
        token = trace_id_var.set(tid)
        try:
            request.trace_id = tid
            response = await self.get_response(request)
            response[RESP_HEADER] = tid
            return response
        finally:
            trace_id_var.reset(token)
