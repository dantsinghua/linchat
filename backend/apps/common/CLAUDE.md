# common 模块指南

> 通用工具模块，提供跨应用共享的中间件、装饰器、异常类、响应工具和事件服务。

## 文件结构

| 文件 | 职责 |
|------|------|
| `middleware.py` | Token 认证中间件（`TokenAuthMiddleware`），从 httpOnly Cookie 读取 Token 并设置 `request.user_id` |
| `decorators.py` | `async_csrf_exempt` — 为 ASGI 原生异步视图跳过 CSRF 检查 |
| `exceptions.py` | 自定义异常类层级（`LLMConnectionError`, `LLMTimeoutError`, `LLMRateLimitError`, `LLMContentFilterError`, `BusinessException` 等） |
| `responses.py` | `ApiResponse` 统一响应工具（`success()`, `error()`, `validation_error()`, `not_found()`, `forbidden()`） |
| `event_service.py` | `EventService` — 基于 Redis PubSub 的 SSE 事件推送服务 |
| `gateway_utils.py` | Gateway 共享工具（请求头构建、错误解析、重试装饰器、Langfuse span） |
| `tokenizer.py` | Token 计数工具（基于 tiktoken） |
| `views.py` | SSE 事件流视图 + 认证相关视图 |
| `urls.py` | 认证路由（`/api/v1/auth/`） |

## EventService (event_service.py)

基于 Redis Pub/Sub 的事件推送服务，支持以下事件类型：

| 事件类型 | 说明 |
|---------|------|
| `logout` | 登出事件（SSO 冲突、Token 过期、管理员踢出） |
| `message` | 消息事件 |
| `heartbeat` | 心跳保活（30 秒间隔） |
| `context_status` | 上下文状态事件 |
| `inference_cancel` | 推理取消事件 |
| `doc_parse_progress` | 文档解析进度事件 |

关键方法：
- `EventService.publish_event(user_id, event_type, data)` — 发布事件
- `EventService.subscribe_user_events(user_id)` — 订阅用户事件流（异步生成器）

## Gateway 工具 (gateway_utils.py)

| 函数/类 | 说明 |
|---------|------|
| `build_gateway_headers(request_id)` | 构建 Authorization + X-Request-ID 请求头 |
| `get_gateway_url()` | 获取 Gateway URL（未配置抛 LLMConnectionError） |
| `parse_gateway_error(response)` | 解析 Gateway 错误响应为 GatewayError |
| `map_httpx_exception(e)` | httpx 异常映射为 LLM 标准异常 |
| `gateway_retry(max_retries=3)` | tenacity 指数退避重试装饰器 |
| `record_gateway_span(...)` | 记录 Langfuse span |

## 异常体系 (exceptions.py)

| 异常类 | 说明 | 重试策略 |
|--------|------|---------|
| `LLMConnectionError` | 连接失败 | 重试 3 次 |
| `LLMTimeoutError` | 请求超时 | 重试 3 次 |
| `LLMRateLimitError` | 频率限制 (429) | 不重试 |
| `LLMContentFilterError` | 内容过滤 | 不重试 |
| `BusinessException` | 业务异常基类 | - |
| `AuthException` → 子类 | 认证相关异常 | - |

## ApiResponse 格式

```json
{"code": "SUCCESS", "message": "...", "data": {...}}
{"code": "ERROR_CODE", "message": "...", "data": null}
```

## 认证机制

- Token 存储在 httpOnly Cookie 中（禁止 localStorage）
- `TokenAuthMiddleware` 每次请求校验 Cookie → 设置 `request.user_id` / `request.user_type`
- SSE 视图使用 `async_csrf_exempt` 绕过 CSRF（Cookie 自动携带）


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1053 | 11:03 AM | 🔵 | gateway_utils.py Imports Constitutional Exception Classes | ~569 |
| #1045 | 11:00 AM | 🔵 | Gateway Utils Already Defines Required LLM Exception Classes | ~577 |
</claude-mem-context>