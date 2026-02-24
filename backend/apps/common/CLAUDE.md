# common 模块指南

> `apps/common` 通用工具模块，提供跨应用共享的中间件、异常体系、响应格式、事件服务、Gateway 工具和 Token 计数。

---

## 模块职责

纯工具模块，无数据模型。为其他所有 App 提供基础设施:
- Token 认证中间件
- WebSocket Token 认证中间件
- 自定义异常类层级
- 统一 API 响应格式
- SSE 事件推送服务
- LLM Gateway 调用工具
- tiktoken Token 计数
- 异步 CSRF 豁免装饰器

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `middleware.py` | `TokenAuthMiddleware` 认证中间件 + Cookie 工具函数 |
| `websocket_auth.py` | `WebSocketTokenAuthMiddleware` WebSocket Token 认证 ASGI 中间件 |
| `exceptions.py` | 自定义异常类层级（认证/LLM/业务） + DRF 异常处理器 |
| `responses.py` | `api_response()` / `error_response()` + `ApiResponse` 类 |
| `event_service.py` | `EventService` 基于 Redis Pub/Sub 的 SSE 事件推送 |
| `gateway_utils.py` | Gateway 请求头构建、错误解析、重试装饰器、Langfuse span |
| `tokenizer.py` | tiktoken Token 计数工具 |
| `decorators.py` | `async_csrf_exempt` 异步 CSRF 豁免装饰器 |
| `views.py` | `EventsView` SSE 事件流视图 |
| `urls.py` | 路由: `GET /api/v1/events` |
| `apps.py` | Django App 配置 |

---

## 认证中间件 (middleware.py)

### TokenAuthMiddleware

同步中间件（Django 自动在线程池中运行以支持异步视图）。

**工作流程**:
1. 检查路径是否为公开路径（跳过认证）
2. 从 httpOnly Cookie (`linchat_token`) 读取 Token
3. SM4 解密验证 Token 格式有效性
4. 计算 SHA256 Hash，从 Redis 获取 Token 数据
5. 检查绝对过期（24 小时）
6. 刷新无操作过期 TTL（取 idle_ttl 和剩余绝对时间的较小值）
7. 设置 `request.user_id` / `request.username` / `request.user_type` / `request.token_hash`

### 公开路径（免认证）

```python
PUBLIC_PATHS = [
    "/api/v1/auth/captcha",
    "/api/v1/auth/login",
    "/api/v1/health/",
    "/admin/",
    "/static/",
]
```

### Cookie 工具函数

| 函数 | 说明 |
|------|------|
| `set_token_cookie(response, token, max_age=3600)` | 设置 httpOnly Cookie |
| `clear_token_cookie(response)` | 删除 Token Cookie |

---

## WebSocket 认证中间件 (websocket_auth.py)

### WebSocketTokenAuthMiddleware

ASGI 中间件，为 WebSocket 连接提供 Token 认证。替代 Django Channels 的 `AuthMiddlewareStack`，兼容 LinChat 的 SM4 Token-in-Cookie 认证机制。

**工作流程**:
1. 从 ASGI `scope['headers']` 解析 Cookie 获取 `linchat_token`
2. SM4 解密验证 Token 格式
3. 计算 SHA256 Hash，从 Redis 查询 Token 数据
4. 检查 24h 绝对过期（`AUTH_TOKEN_ABSOLUTE_TTL`）
5. 刷新无操作 TTL（取 idle_ttl 和剩余绝对时间的较小值）
6. 设置 `scope['user_id']` / `scope['username']` / `scope['user_type']`
7. 失败时发送 `websocket.close(code=4001)`

**注意**:
- 仅处理 `scope["type"] == "websocket"` 的连接，非 WebSocket 请求直接透传
- 不处理设备 API Token 认证（由 VoiceConsumer 的 `connect()` 方法中处理）
- 内部使用 `_WebSocketAuthError` 异常类（仅中间件内部使用，不对外暴露）

---

## 异常体系 (exceptions.py)

### 异常层级

```
AppException (基类)
├── AuthException (401)
│   ├── AuthFailedException (400) — 用户名/密码错误
│   ├── CaptchaInvalidException (400) — 验证码错误/过期
│   ├── TokenExpiredException (401) — Token 已过期
│   ├── AccountLockedException (403) — 账户锁定（含 remaining_seconds）
│   └── UserDisabledException (403) — 账户禁用
├── LLMException (503)
│   ├── LLMConnectionError — 连接失败（重试 3 次）
│   ├── LLMTimeoutError — 请求超时（重试 3 次）
│   ├── LLMRateLimitError (429) — 频率限制（不重试，含 retry_after）
│   ├── LLMContentFilterError (400) — 内容过滤（不重试）
│   ├── LLMInvalidResponseError — 无效响应（重试 3 次）
│   ├── LLMQuotaExceededError (402) — 配额用尽
│   └── LLMContextLengthError (400) — 上下文长度超限
├── ExternalServiceError (502) — 外部服务异常
└── BusinessException (400)
    ├── MessageTooLongException — 消息过长
    └── EmptyMessageException — 消息为空
```

### DRF 异常处理器

`custom_exception_handler(exc, context)`: 注册在 `settings.REST_FRAMEWORK["EXCEPTION_HANDLER"]`，将 `AppException` 子类统一转为 `{"code": "...", "message": "...", "data": null}` 格式。

---

## 响应格式 (responses.py)

### Django JsonResponse 版本（用于异步视图）

```python
api_response(data=None, message="操作成功", code="SUCCESS", status_code=200)
error_response(message="操作失败", code="ERROR", status_code=400, extra=None)
```

### DRF Response 版本（用于 DRF 视图）

```python
ApiResponse.success(data, message, status_code)
ApiResponse.created(data, message)
ApiResponse.error(message, code, data, status_code)
ApiResponse.validation_error(message, errors)
ApiResponse.not_found(message)
ApiResponse.unauthorized(message)
ApiResponse.forbidden(message)
ApiResponse.paginated(items, total, page, page_size)
ApiResponse.cursor_paginated(items, next_cursor, has_more)
```

统一格式: `{"code": "SUCCESS|ERROR_CODE", "message": "...", "data": {...}}`

---

## SSE 事件服务 (event_service.py)

### EventService

基于 Redis Pub/Sub 的事件推送服务，频道: `events:user:{user_id}`。

### 事件类型

| EventType | 说明 |
|-----------|------|
| `logout` | 登出事件（SSO 冲突/Token 过期/管理员踢出） |
| `message` | 消息事件 |
| `heartbeat` | 心跳保活（30 秒间隔） |
| `context_status` | 上下文状态事件 |
| `inference_cancel` | 推理取消事件 |
| `doc_parse_progress` | 文档解析进度事件 |

### 登出原因

| LogoutReason | 消息 |
|-------------|------|
| `SSO_CONFLICT` | 您已在其他设备登录 |
| `TOKEN_EXPIRED` | 登录已过期 |
| `ADMIN_KICK` | 您已被管理员踢出 |

### 关键方法

```python
await EventService.publish_event(user_id, event_type, data)       # 发布事件
await EventService.publish_logout_event(user_id, LogoutReason.XXX) # 发布登出事件
async for event in EventService.subscribe_user_events(user_id):    # 订阅事件流
    yield event
```

### SSE 视图 (views.py)

`EventsView` (GET /api/v1/events): ASGI 原生异步 SSE 视图，返回 `StreamingHttpResponse(content_type="text/event-stream")`。

---

## Gateway 工具 (gateway_utils.py)

为 InferenceService / DocumentParseService 提供共享的 Gateway HTTP 调用工具。

| 函数/类 | 说明 |
|---------|------|
| `build_gateway_headers(request_id)` | 构建 `Authorization: Bearer {key}` + `X-Request-ID` 请求头 |
| `get_gateway_url()` | 获取 `LLM_GATEWAY_URL`（未配置抛 `LLMConnectionError`） |
| `parse_gateway_error(response)` | 解析 `{"error": {"code": "Exxxx", "message": "..."}}` 为 `GatewayError` |
| `map_httpx_exception(e)` | httpx 异常映射为 LLM 标准异常（Timeout/Connect/429/400） |
| `gateway_retry(max_retries=3)` | tenacity 指数退避重试装饰器（仅重试 Connection/Timeout） |
| `record_gateway_span(...)` | 记录 Langfuse span（request_type, model, duration, status_code） |

---

## Token 计数 (tokenizer.py)

基于 tiktoken 的 Token 计数工具，使用 `cl100k_base` 编码（单例模式延迟初始化）。

| 函数 | 说明 |
|------|------|
| `count_tokens(text)` | 计算文本 Token 数（失败时回退到 `len(text) // 4`） |
| `count_messages_tokens(messages)` | 计算消息列表总 Token 数（含 per-message 开销 +4 和 reply 开销 +2） |

---

## 异步 CSRF 装饰器 (decorators.py)

`async_csrf_exempt(view_func)`: 自动检测视图函数类型，返回对应的同步/异步 wrapper 并设置 `csrf_exempt = True`。用于 ASGI 原生异步视图。
