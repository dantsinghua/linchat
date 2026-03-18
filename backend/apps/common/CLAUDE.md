# common 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 模块职责

纯工具模块，无数据模型。为所有 App 提供基础设施：认证中间件、异常体系、响应格式、SSE 工具、Gateway 调用、存储封装、速率限制、Token 计数、异步任务工具。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `async_utils.py` | 异步任务取消工具：`cancel_task(task)` 异步取消 + `cancel_task_sync(task)` 同步取消（仅调 cancel，不 await），统一处理 None/CancelledError |
| `middleware.py` | `TokenAuthMiddleware` -- Cookie SM4 Token 认证（同步中间件）、`set_token_cookie()`/`clear_token_cookie()` |
| `websocket_auth.py` | `WebSocketTokenAuthMiddleware` -- ASGI WebSocket Cookie Token 认证（SM4 解密 + Redis 验证 + 滑动过期），失败发送 4001 关闭码 |
| `exceptions.py` | 自定义异常层级（Auth/LLM/Business/ExternalService）、`map_llm_exception()` 异常映射、DRF `custom_exception_handler` |
| `responses.py` | `api_response()`/`error_response()` (JsonResponse) + `ApiResponse` 类 (DRF Response) |
| `event_service.py` | `EventService` -- Redis Pub/Sub SSE 事件推送（EventType: logout/message/heartbeat/context_status/inference_cancel/doc_parse_progress）、`SSEEvent` 格式化 |
| `gateway_utils.py` | Gateway HTTP 工具集（见下方详情） |
| `tokenizer.py` | tiktoken Token 计数：`count_tokens()`、`count_messages_tokens()`（cl100k_base 编码，单例模式） |
| `decorators.py` | `async_csrf_exempt` -- 异步兼容 CSRF 豁免装饰器 |
| `sse.py` | SSE 视图辅助（从 `apps.chat.sse` 迁移）：`parse_sse_request()`、`make_sse_response()`、`first_validation_error()` |
| `rate_limiter.py` | Redis INCR 通用速率限制：`check_rate_limit(key, limit, window)` -> (allowed, count) |
| `storage/__init__.py` | storage 子包，导出 `MinioService`、`minio_service` 单例 |
| `storage/minio_service.py` | MinIO 对象存储封装（从 `apps.chat.services.minio_service` 迁移）：upload_file/upload_bytes/download_file/get_object_stream/delete_file/file_exists/get_presigned_url/ensure_bucket_exists |
| `views.py` | `EventsView` -- ASGI 异步 SSE 事件流视图（Redis Pub/Sub 订阅 + 30s 心跳） |
| `urls.py` | 路由: `GET /api/v1/events` |
| `apps.py` | Django App 配置 |

---

## async_utils.py 详情

```python
async def cancel_task(task: Optional[asyncio.Task]) -> None
    # 异步取消：task.cancel() + await（吞 CancelledError），支持 None 入参
    # 用于 cleanup 路径的优雅取消

def cancel_task_sync(task: Optional[asyncio.Task]) -> None
    # 同步取消：仅调用 task.cancel()，不 await
    # 用于非 async 上下文或不需要等待取消完成的场景
```

**使用场景**: voice 模块 Consumer Mixin 中统一取消 asyncio.Task（segment_timer、idle_check、pipeline_task 等），替代各处重复的 `if task: task.cancel()` 代码。

---

## gateway_utils.py 详情

| 函数/类 | 说明 |
|---------|------|
| `build_gateway_headers(request_id)` | 构建 Gateway 请求头（Authorization + X-Request-ID） |
| `get_gateway_url()` | 获取 LLM_GATEWAY_URL 配置 |
| `GatewayError` | Gateway 错误数据类（code/message/details/http_status） |
| `parse_gateway_error(response)` | 解析 httpx.Response 中的 Gateway 错误 |
| `map_httpx_exception(e)` | httpx 异常映射到 LLM 异常类（Timeout/Connect/429/400） |
| `gateway_retry(max_retries, retry_on)` | tenacity 重试装饰器（指数退避，默认重试 LLMConnectionError/LLMTimeoutError） |
| `_get_langfuse()` | Langfuse 客户端单例（模块级缓存，避免每次 span 重建连接） |
| `record_gateway_span(...)` | Langfuse span 记录（使用 `start_span()`，不同步 flush，由 BatchSpanProcessor 批量导出） |

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
│   ├── LLMRateLimitError (429, 不重试, 含 retry_after)
│   ├── LLMContentFilterError (400, 不重试)
│   ├── LLMInvalidResponseError (重试3次)
│   ├── LLMQuotaExceededError (402)
│   └── LLMContextLengthError (400)
├── ExternalServiceError (502)
└── BusinessException (400)
    ├── MessageTooLongException
    └── EmptyMessageException
```

`map_llm_exception(e)`: 将各种异常映射到 LLM 异常类，供 Agent 执行层使用。

---

## WebSocket 认证流程

`WebSocketTokenAuthMiddleware` 认证流程：

```
WebSocket 连接 → 提取 Cookie 中的 linchat_token
  → SM4 解密验证 → Redis 查询 token_hash
  → 检查 24h 绝对超时 → 滑动续期 idle TTL
  → 注入 scope["user_id"]/scope["username"]/scope["user_type"]
  → 失败: 发送 websocket.close code=4001
```

仅处理 `scope["type"] == "websocket"`；非 WebSocket 直接透传。设备 API Token 认证由 `VoiceConsumer` 单独处理。

---

## 注意事项

1. `record_gateway_span()` 使用 Langfuse 3.x `start_span()` API（非已废弃的 `trace()`），Langfuse 客户端为模块级单例（`_langfuse_client`），不同步 flush
2. `check_rate_limit()` 内部做异常兜底，Redis 故障时默认放行（返回 True）
3. `make_sse_response()` 依赖 `apps.chat.services.types.StreamChunk` 数据类
4. 新代码应直接 import `apps.common.storage`/`apps.common.sse`，避免经过 chat 旧兼容层
5. `MinioService` 为懒初始化单例（`minio_service` 全局实例），首次调用 `.client` 属性时创建连接
6. `cancel_task()` / `cancel_task_sync()` 用于统一异步任务取消，voice 模块各 Mixin 广泛使用
