"""WebSocketTokenAuthMiddleware._extract_token_from_headers 单元测试。

覆盖 batch-23 收窄路径：畸形 Cookie 触发 http.cookies.CookieError 时应安全回退为 None。
"""

from apps.common.websocket_auth import TOKEN_COOKIE_NAME, WebSocketTokenAuthMiddleware


def _make_middleware() -> WebSocketTokenAuthMiddleware:
    return WebSocketTokenAuthMiddleware(app=lambda *a, **k: None)


def _scope_with_cookie(cookie_bytes: bytes) -> dict:
    return {"type": "websocket", "headers": [(b"cookie", cookie_bytes)]}


def test_extract_token_malformed_cookie_returns_none():
    # 非法 key ')(' 会让 SimpleCookie.load 抛 CookieError（收窄后仍被捕获）
    mw = _make_middleware()
    assert mw._extract_token_from_headers(_scope_with_cookie(b")(=1")) is None


def test_extract_token_valid_cookie_returns_value():
    mw = _make_middleware()
    scope = _scope_with_cookie(f"{TOKEN_COOKIE_NAME}=abc123".encode())
    assert mw._extract_token_from_headers(scope) == "abc123"


def test_extract_token_no_cookie_header_returns_none():
    mw = _make_middleware()
    assert mw._extract_token_from_headers({"type": "websocket", "headers": []}) is None


def test_extract_token_missing_token_cookie_returns_none():
    mw = _make_middleware()
    assert mw._extract_token_from_headers(_scope_with_cookie(b"other=1")) is None
