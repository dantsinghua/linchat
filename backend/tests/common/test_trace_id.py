"""
trace_id 中间件 + 日志注入测试 (batch-04)

覆盖：
- T1: TraceIdMiddleware 无 header → 自动生成 32 字符 UUID hex
- T2: TraceIdMiddleware 有 header → 继承该值
- T3: TraceIdMiddleware > 128 字符 header → 丢弃重新生成
- T4: TraceIdMiddleware 响应头 X-Request-ID 回写
- T5: TraceIdFilter.filter() 空 contextvars 注入 "-"
- T6: JSONFormatter.format() 输出合法 JSON（json.loads round-trip）
"""
from __future__ import annotations

import json
import logging
import re

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.common import trace_id_var
from core.logging_config import JSONFormatter, TraceIdFilter
from core.middleware import RESP_HEADER, TraceIdMiddleware


UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _reset_trace_id_var():
    # 本套件共享 event loop，middleware 不再 reset（见模块 docstring），
    # 需要每个 test 开头置空，避免前后测试相互污染。
    token = trace_id_var.set("")
    yield
    trace_id_var.reset(token)


# ============ TraceIdMiddleware 测试 ============


class TestTraceIdMiddleware:
    """TraceIdMiddleware 同步路径行为测试"""

    def setup_method(self):
        self.rf = RequestFactory()

    def _make_mw(self, captured: dict):
        """构造同步中间件，同时捕获请求内 trace_id。"""
        def get_response(request):
            captured["request_trace_id"] = getattr(request, "trace_id", None)
            captured["contextvar_trace_id"] = trace_id_var.get()
            return HttpResponse("ok")
        return TraceIdMiddleware(get_response)

    def test_t1_no_header_generates_uuid_hex(self):
        """T1: 无 X-Request-ID header → 生成 32 字符 UUID hex"""
        captured: dict = {}
        mw = self._make_mw(captured)
        request = self.rf.get("/ping")
        response = mw(request)

        assert response.status_code == 200
        tid = response[RESP_HEADER]
        assert UUID_HEX_RE.match(tid), f"Expected 32-char hex, got: {tid!r}"
        assert captured["request_trace_id"] == tid
        assert captured["contextvar_trace_id"] == tid
        # middleware 返回后 contextvar 必须仍保留（uvicorn.access / django.request
        # 在 middleware 返回之后才写日志，依赖此值）
        assert trace_id_var.get() == tid

    def test_t2_inherits_incoming_header(self):
        """T2: 有 X-Request-ID header → 原样继承"""
        captured: dict = {}
        mw = self._make_mw(captured)
        custom = "custom-trace-001-abc"
        request = self.rf.get("/ping", HTTP_X_REQUEST_ID=custom)
        response = mw(request)

        assert response[RESP_HEADER] == custom
        assert captured["request_trace_id"] == custom
        assert captured["contextvar_trace_id"] == custom

    def test_t3_over_128_chars_discarded(self):
        """T3: >128 字符 header → 丢弃重新生成"""
        captured: dict = {}
        mw = self._make_mw(captured)
        too_long = "a" * 129
        request = self.rf.get("/ping", HTTP_X_REQUEST_ID=too_long)
        response = mw(request)

        tid = response[RESP_HEADER]
        assert tid != too_long
        assert UUID_HEX_RE.match(tid), f"Expected regenerated UUID hex, got: {tid!r}"

    def test_t4_response_header_written(self):
        """T4: 响应头 X-Request-ID 必须与请求 trace_id 一致"""
        captured: dict = {}
        mw = self._make_mw(captured)
        request = self.rf.get("/ping", HTTP_X_REQUEST_ID="resp-header-check")
        response = mw(request)

        assert RESP_HEADER in response
        assert response[RESP_HEADER] == "resp-header-check"
        # 请求对象也应已设置
        assert captured["request_trace_id"] == "resp-header-check"


# ============ TraceIdFilter 测试 ============


class TestTraceIdFilter:
    """TraceIdFilter 行为测试"""

    def test_t5_empty_contextvar_injects_dash(self):
        """T5: 空 contextvars → 注入 '-'"""
        # 确保当前上下文无 trace_id
        token = trace_id_var.set("")
        try:
            flt = TraceIdFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname=__file__,
                lineno=1, msg="hello", args=(), exc_info=None,
            )
            allow = flt.filter(record)
            assert allow is True
            assert record.trace_id == "-"
        finally:
            trace_id_var.reset(token)

    def test_t5b_nonempty_contextvar_injects_value(self):
        """T5b: 非空 contextvars → 注入实际值"""
        token = trace_id_var.set("tid-123")
        try:
            flt = TraceIdFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname=__file__,
                lineno=1, msg="hello", args=(), exc_info=None,
            )
            flt.filter(record)
            assert record.trace_id == "tid-123"
        finally:
            trace_id_var.reset(token)


# ============ JSONFormatter 测试 ============


class TestJSONFormatter:
    """JSONFormatter 输出格式测试"""

    def test_t6_output_is_valid_json(self):
        """T6: format() 必须输出合法 JSON，字段齐全"""
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="apps.chat", level=logging.INFO, pathname=__file__,
            lineno=42, msg="hello %s", args=("world",), exc_info=None,
        )
        record.trace_id = "tid-xyz"
        record.user_id = 7
        record.duration_ms = 123

        out = fmt.format(record)
        payload = json.loads(out)  # round-trip：能解析即合法

        assert payload["level"] == "INFO"
        assert payload["logger"] == "apps.chat"
        assert payload["trace_id"] == "tid-xyz"
        assert payload["msg"] == "hello world"
        assert payload["lineno"] == 42
        # extra 字段必须保留
        assert payload["user_id"] == 7
        assert payload["duration_ms"] == 123

    def test_t6b_non_json_extra_fallback_to_repr(self):
        """T6b: 非 JSON 可序列化 extra → repr() 兜底，永不丢日志"""
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname=__file__,
            lineno=1, msg="x", args=(), exc_info=None,
        )
        record.weird = object()  # 无法 json.dumps
        out = fmt.format(record)
        payload = json.loads(out)
        assert isinstance(payload["weird"], str)
        assert "object" in payload["weird"]

    def test_t6c_exc_info_formatted(self):
        """T6c: exc_info 存在时 → payload 包含 exc_info 字符串"""
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__,
            lineno=1, msg="err", args=(), exc_info=exc_info,
        )
        out = fmt.format(record)
        payload = json.loads(out)
        assert "exc_info" in payload
        assert "ValueError" in payload["exc_info"]
        assert "boom" in payload["exc_info"]


# ============ async 路径 ============


@pytest.mark.asyncio
async def test_async_middleware_path():
    """补充：ASGI async 路径也能正确 set trace_id 且离开 middleware 后仍保留"""
    captured: dict = {}

    async def get_response(request):
        captured["request_trace_id"] = getattr(request, "trace_id", None)
        captured["contextvar_trace_id"] = trace_id_var.get()
        return HttpResponse("ok")

    mw = TraceIdMiddleware(get_response)
    rf = RequestFactory()
    request = rf.get("/async-ping", HTTP_X_REQUEST_ID="async-tid-42")
    response = await mw(request)

    assert response[RESP_HEADER] == "async-tid-42"
    assert captured["request_trace_id"] == "async-tid-42"
    assert captured["contextvar_trace_id"] == "async-tid-42"
    # 保留不 reset：外层 uvicorn/django 日志依赖此值
    assert trace_id_var.get() == "async-tid-42"


# ============ 回归保护：uvicorn.access / django.request log 在 middleware 返回后读 ============


class TestTraceIdPersistsAfterMiddleware:
    """batch-04 修复 bug：middleware 不得在 finally 里 reset。
    外层 uvicorn.access 与 django.request log_response 在 middleware 返回后发出日志，
    依赖 contextvar 仍存活。回归保护：任何未来的"重新引入 reset"改动会立即炸掉这两项。
    """

    def test_sync_trace_id_outlives_middleware_return(self):
        rf = RequestFactory()
        req = rf.get("/ping", HTTP_X_REQUEST_ID="persist-sync-001")
        mw = TraceIdMiddleware(lambda r: HttpResponse("ok"))
        response = mw(req)
        assert response[RESP_HEADER] == "persist-sync-001"
        # 模拟外层（uvicorn / django log_response）在 middleware 返回后读
        assert trace_id_var.get() == "persist-sync-001"

    @pytest.mark.asyncio
    async def test_async_trace_id_outlives_middleware_return(self):
        async def get_response(r):
            return HttpResponse("ok")
        rf = RequestFactory()
        req = rf.get("/ping", HTTP_X_REQUEST_ID="persist-async-002")
        mw = TraceIdMiddleware(get_response)
        response = await mw(req)
        assert response[RESP_HEADER] == "persist-async-002"
        # asyncio.Task 级持久性：本 task 内后续日志（uvicorn access）仍能拿到 tid
        assert trace_id_var.get() == "persist-async-002"
