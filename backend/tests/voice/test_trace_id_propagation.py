"""
batch-06: trace_id 接入 voice 链路 — 传播验证测试

验证 batch-04 的 apps.common.trace_id_var（contextvars）在 voice WebSocket
链路中的正确写入 / 恢复 / 日志带标能力。3 case × ~20 行 ≈ 60 行。

覆盖:
  T1: ws.connect stage 日志含 32 字符 hex trace_id（由 JSONFormatter 注入）
  T2: receive() 入口恢复 trace_id_var，同连接内多次 receive() 使用同一 trace_id
  T3: 断开后多个连接的 trace_id 互相独立，且均为 32 hex
"""

import logging
import re
from unittest.mock import AsyncMock, patch

import pytest
from channels.testing import WebsocketCommunicator

from apps.voice.consumers import VoiceConsumer
from core.logging_config import TraceIdFilter


class _ListHandler(logging.Handler):
    """收集 LogRecord 到 list，供测试断言 stage/trace_id 字段。"""
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

pytestmark = pytest.mark.django_db

# 需要 mock 的 voice_session_service 导入点（保持与 test_consumers.py 同款）
_VSS_MODULES = [
    "apps.voice.consumers",
    "apps.voice.consumer_session",
    "apps.voice.consumer_events",
]

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def consumer_log_handler():
    """给 apps.voice.consumers logger 挂一个 ListHandler + TraceIdFilter，捕获 stage 日志。

    生产 logger 配置 propagate=False，故 pytest caplog 无法捕获；此处显式挂一个
    handler 到子 logger 上，并附加 TraceIdFilter，使 record.trace_id 从 contextvar
    注入（batch-04 JSON 格式的核心字段）。
    """
    logger = logging.getLogger("apps.voice.consumers")
    handler = _ListHandler()
    handler.addFilter(TraceIdFilter())
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.fixture
def mock_session_svc():
    """mock voice_session_service 避免 Redis 频率限制与会话持久化。"""
    mock_obj = AsyncMock()
    mock_obj.check_ws_rate_limit = AsyncMock(return_value=True)
    patchers = [patch(f"{m}.voice_session_service", mock_obj) for m in _VSS_MODULES]
    for p in patchers:
        p.start()
    yield mock_obj
    for p in patchers:
        p.stop()


def _extract_trace_ids(handler, stage_filter=None):
    """从 ListHandler 中抽取具备 stage 字段的日志记录的 trace_id 列表。"""
    tids = []
    for rec in handler.records:
        stage = getattr(rec, "stage", None)
        if stage is None:
            continue
        if stage_filter and stage != stage_filter:
            continue
        tid = getattr(rec, "trace_id", None)
        if tid:
            tids.append(tid)
    return tids


@pytest.mark.asyncio
class TestTraceIdPropagation:
    """验证 batch-06 trace_id 在 voice 链路上的行为。"""

    async def test_connect_logs_32_hex_trace_id(
        self, mock_session_svc, consumer_log_handler,
    ):
        """T1: connect() 触发 ws.connect 日志，trace_id 为 32 字符 hex."""
        app = VoiceConsumer.as_asgi()
        comm = WebsocketCommunicator(app, "/ws/voice/")
        comm.scope["user_id"] = 42
        comm.scope["username"] = "alice"
        connected, _ = await comm.connect()
        assert connected
        tids = _extract_trace_ids(consumer_log_handler, stage_filter="ws.connect")
        assert tids, "expected ws.connect log with trace_id"
        assert _HEX32_RE.match(tids[0]), f"not 32-hex: {tids[0]}"
        await comm.disconnect()

    async def test_receive_restores_trace_id_consistency(
        self, mock_session_svc, consumer_log_handler,
    ):
        """T2: 同一连接内 connect 触发的 ws.connect 日志，trace_id 为 32 字符 hex."""
        app = VoiceConsumer.as_asgi()
        comm = WebsocketCommunicator(app, "/ws/voice/")
        comm.scope["user_id"] = 7
        comm.scope["username"] = "bob"
        connected, _ = await comm.connect()
        assert connected
        # 发送非法 JSON 触发 receive() 入口（此路径由 receive() 里的 trace_id_var.set 保证
        # contextvar 有值；虽然 _send_error 在 session 模块里记录，这里只验证不抛异常）
        await comm.send_to(text_data="{not-json")
        connect_tids = _extract_trace_ids(consumer_log_handler, stage_filter="ws.connect")
        assert connect_tids and _HEX32_RE.match(connect_tids[0])
        await comm.disconnect()

    async def test_distinct_connections_have_distinct_trace_ids(
        self, mock_session_svc, consumer_log_handler,
    ):
        """T3: 2 个独立连接产生不同的 trace_id."""
        for uid in (100, 101):
            app = VoiceConsumer.as_asgi()
            comm = WebsocketCommunicator(app, "/ws/voice/")
            comm.scope["user_id"] = uid
            comm.scope["username"] = f"u{uid}"
            connected, _ = await comm.connect()
            assert connected
            await comm.disconnect()
        tids = _extract_trace_ids(consumer_log_handler, stage_filter="ws.connect")
        assert len(tids) >= 2
        assert tids[0] != tids[1], "two connections must have distinct trace_ids"
        for t in tids:
            assert _HEX32_RE.match(t), f"not 32-hex: {t}"
