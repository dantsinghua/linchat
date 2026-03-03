# common 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 模块职责

纯工具模块，无数据模型。为所有 App 提供基础设施。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `middleware.py` | `TokenAuthMiddleware` — Cookie SM4 Token 认证（同步中间件）、`set_token_cookie()`/`clear_token_cookie()` |
| `websocket_auth.py` | `WebSocketTokenAuthMiddleware` — ASGI WebSocket Token 认证，SM4+Redis 验证，失败发送 4001 关闭 |
| `exceptions.py` | 自定义异常层级（Auth/LLM/Business）、`map_llm_exception()` 异常映射、DRF `custom_exception_handler` |
| `responses.py` | `api_response()`/`error_response()` (JsonResponse) + `ApiResponse` 类 (DRF Response) |
| `event_service.py` | `EventService` — Redis Pub/Sub SSE 事件推送（logout/message/heartbeat/context_status/inference_cancel/doc_parse_progress） |
| `gateway_utils.py` | Gateway HTTP 工具：`build_gateway_headers()`、`parse_gateway_error()`、`map_httpx_exception()`、`gateway_retry()` tenacity 装饰器、`record_gateway_span()` Langfuse |
| `tokenizer.py` | tiktoken Token 计数：`count_tokens()`、`count_messages_tokens()`（cl100k_base 编码，单例） |
| `decorators.py` | `async_csrf_exempt` — 异步兼容 CSRF 豁免装饰器 |
| `sse.py` | **新增** — SSE 视图辅助函数（从 `apps.chat.sse` 迁移）：`parse_sse_request()`、`make_sse_response()`、`first_validation_error()` |
| `rate_limiter.py` | **新增** — Redis INCR 通用速率限制：`check_rate_limit(key, limit, window)` |
| `storage/__init__.py` | **新增** — storage 子包，导出 MinioService |
| `storage/minio_service.py` | **新增** — MinIO 对象存储封装（从 `apps.chat.services.minio_service` 迁移）：upload/download/delete/presigned_url/ensure_bucket |
| `views.py` | `EventsView` — ASGI 异步 SSE 事件流视图 |
| `urls.py` | 路由: `GET /api/v1/events` |
| `apps.py` | Django App 配置 |

---

## 异常层级

```
AppException
├── AuthException (401)
│   ├── AuthFailedException (400)
│   ├── CaptchaInvalidException (400)
│   ├── TokenExpiredException (401)
│   ├── AccountLockedException (403)
│   └── UserDisabledException (403)
├── LLMException (503)
│   ├── LLMConnectionError (重试3次)
│   ├── LLMTimeoutError (重试3次)
│   ├── LLMRateLimitError (429, 不重试)
│   ├── LLMContentFilterError (400, 不重试)
│   ├── LLMInvalidResponseError (重试3次)
│   ├── LLMQuotaExceededError (402)
│   └── LLMContextLengthError (400)
├── ExternalServiceError (502)
└── BusinessException (400)
    ├── MessageTooLongException
    └── EmptyMessageException
```

---

## 新增模块说明

- **`storage/minio_service.py`**: MinIO 封装（从 `apps.chat.services.minio_service` 迁移），提供 upload/download/delete/presigned_url/ensure_bucket。单例: `from apps.common.storage import minio_service`
- **`sse.py`**: SSE 辅助函数（从 `apps.chat.sse` 迁移），提供 `parse_sse_request()`、`make_sse_response()`、`first_validation_error()`
- **`rate_limiter.py`**: 通用速率限制 `check_rate_limit(key, limit, window)`
- **`map_llm_exception()`**: 已从 `apps.chat.services.generation` 迁移到 `exceptions.py`

---

## 注意事项

1. WebSocket 中间件仅处理 `scope["type"] == "websocket"`，设备 API Token 认证由 VoiceConsumer 处理
2. 新代码应直接 import 迁移后的位置（如 `apps.common.storage`），避免经过旧兼容层


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1053 | 11:03 AM | 🔵 | gateway_utils.py Imports Constitutional Exception Classes | ~569 |
| #1045 | 11:00 AM | 🔵 | Gateway Utils Already Defines Required LLM Exception Classes | ~577 |
</claude-mem-context>