# common 模块指南

## 模块概述

通用工具模块，提供跨应用共享的中间件、装饰器、异常类和响应工具。

## 文件结构

| 文件 | 职责 |
|------|------|
| `middleware.py` | Token 认证中间件（`TokenAuthMiddleware`），从 httpOnly Cookie 读取 Token 并设置 `request.user_id` |
| `decorators.py` | `async_csrf_exempt` — 为 ASGI 原生异步视图跳过 CSRF 检查 |
| `exceptions.py` | 自定义异常类层级（`LLMConnectionError`, `LLMTimeoutError`, `LLMRateLimitError` 等） |
| `responses.py` | `ApiResponse` 统一响应工具（`success()`, `error()`, `validation_error()`, `not_found()`） |
| `event_service.py` | `EventService` — 基于 Redis PubSub 的 SSE 事件推送服务 |
| `tokenizer.py` | Token 计数工具（基于 tiktoken） |
| `views.py` | 认证相关视图（登录/登出/验证码） |
| `urls.py` | 认证路由 |

## 认证机制

- Token 存储在 httpOnly Cookie 中（禁止 localStorage）
- `TokenAuthMiddleware` 在每次请求中校验 Cookie → 设置 `request.user_id`
- SSE 视图使用 `async_csrf_exempt` 装饰器绕过 CSRF（Cookie 自动携带）

## ApiResponse 格式

所有 API 响应统一格式：
```json
{"code": "SUCCESS", "message": "...", "data": {...}}
```

错误响应：
```json
{"code": "ERROR_CODE", "message": "...", "data": null}
```


<claude-mem-context>

</claude-mem-context>