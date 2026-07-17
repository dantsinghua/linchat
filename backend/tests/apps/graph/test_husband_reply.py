"""老公 channel 内部端点测试 — POST /api/v1/internal/husband/reply/

安全红线：/api/v1/internal/ 跳过 cookie 中间件，view 必须自行校验设备 token，
token 缺失/无效返回 401 且**不调 AgentService.execute**（测试 2/3 锁死）。
聚合语义：content 帧累加为正文；error 帧→502；execute 抛异常→502；空 reply→502。
层1 回声令牌：响应回带 channel + origin_peer。
层3：thread_id=user_{id}_wechat + channel=wechat 透传断言。

Mock 策略：patch internal_views 内引用的 AgentService.execute（返回 async gen）
+ device_service.authenticate_by_token，不触碰 DB / 真实 LLM。
"""
from unittest.mock import AsyncMock, patch

import pytest
from rest_framework.test import APIClient

from apps.common.exceptions import LLMTimeoutError
from apps.graph.services.types import StreamChunk

HUSBAND_URL = "/api/v1/internal/husband/reply/"
USER_ID = 7
_IV = "apps.graph.internal_views"


def _auth_ok():
    return patch(
        f"{_IV}.device_service.authenticate_by_token",
        new_callable=AsyncMock,
        return_value={"user_id": USER_ID, "device_uuid": "dev-uuid", "device_name": "wechat-narrator"},
    )


def _auth_fail():
    return patch(
        f"{_IV}.device_service.authenticate_by_token",
        new_callable=AsyncMock,
        return_value=None,
    )


def _mock_execute(chunks=None, raises=None):
    """patch AgentService.execute → async generator。

    raises 非空 → 迭代时抛（模拟 astream 中途 LLMTimeout 等）。
    否则依次 yield chunks（StreamChunk 列表）。
    返回的 patch 上下文 yield MagicMock，可 assert_not_called / 查 call_args。
    """
    async def _gen(*_args, **_kwargs):
        if raises is not None:
            raise raises
        for c in (chunks or []):
            yield c

    return patch(f"{_IV}.AgentService.execute", side_effect=_gen)


def _body(**over):
    b = {"message": "老公我今天好累", "channel": "wechat", "origin_peer": "灰灰"}
    b.update(over)
    return b


# ---------- 1. 有效 token → 聚合 200 ----------
def test_valid_token_aggregates_reply_200():
    client = APIClient()
    chunks = [
        StreamChunk(type="content", content="辛苦啦"),
        StreamChunk(type="content", content="亲爱滴"),
        StreamChunk(type="done", content=""),
    ]
    with _auth_ok(), _mock_execute(chunks=chunks):
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 200, resp.content
    data = resp.json()["data"]
    assert data["reply"] == "辛苦啦亲爱滴"


# ---------- 2. 缺 token → 401 且不调 execute ----------
def test_missing_token_returns_401_no_execute():
    client = APIClient()
    with _auth_fail(), _mock_execute(chunks=[StreamChunk(type="content", content="x")]) as m:
        resp = client.post(HUSBAND_URL, data=_body(), format="json")
    assert resp.status_code == 401
    m.assert_not_called()


# ---------- 3. 错 token → 401 且不调 execute ----------
def test_invalid_token_returns_401_no_execute():
    client = APIClient()
    with _auth_fail(), _mock_execute(chunks=[StreamChunk(type="content", content="x")]) as m:
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="bad-token")
    assert resp.status_code == 401
    m.assert_not_called()


# ---------- 4. error 帧 → 502 ----------
def test_error_chunk_returns_502():
    client = APIClient()
    chunks = [
        StreamChunk(type="content", content="部分"),
        StreamChunk(type="error", content="gateway 502"),
    ]
    with _auth_ok(), _mock_execute(chunks=chunks):
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 502


# ---------- 5. execute 抛 LLMTimeout → 502 ----------
def test_llm_timeout_returns_502():
    client = APIClient()
    with _auth_ok(), _mock_execute(raises=LLMTimeoutError("AI响应超时")):
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 502


# ---------- 6. 空 reply → 502 ----------
def test_empty_reply_returns_502():
    client = APIClient()
    chunks = [
        StreamChunk(type="content", content="   "),
        StreamChunk(type="done", content=""),
    ]
    with _auth_ok(), _mock_execute(chunks=chunks):
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 502


# ---------- 7. channel + thread_id 透传断言 ----------
def test_channel_and_thread_id_passthrough():
    client = APIClient()
    chunks = [StreamChunk(type="content", content="嗯呐")]
    with _auth_ok(), _mock_execute(chunks=chunks) as m:
        resp = client.post(HUSBAND_URL, data=_body(), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 200
    kwargs = m.call_args.kwargs
    assert kwargs["channel"] == "wechat"
    assert kwargs["thread_id"] == f"user_{USER_ID}_wechat"
    assert kwargs["user_id"] == USER_ID
    assert kwargs["attachment_uuids"] is None


# ---------- 8. 回声令牌回带 ----------
def test_echo_token_returned():
    client = APIClient()
    chunks = [StreamChunk(type="content", content="宝我在")]
    with _auth_ok(), _mock_execute(chunks=chunks):
        resp = client.post(HUSBAND_URL, data=_body(origin_peer="灰灰"), format="json",
                           HTTP_X_DEVICE_TOKEN="valid-token-abc123")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["channel"] == "wechat"
    assert data["origin_peer"] == "灰灰"
    assert data["reply"] == "宝我在"
