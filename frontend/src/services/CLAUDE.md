# services 模块指南

## 服务列表

| 服务 | 职责 |
|------|------|
| `api.ts` | Axios 实例（baseURL、Cookie 认证、401 拦截器） |
| `chatService.ts` | 聊天 API（SSE 流处理、历史消息、停止生成） |
| `mediaApi.ts` | 媒体 API（上传、取消推理、文档解析） |
| `ttsApi.ts` | TTS API（语音合成，使用 fetch 处理二进制响应） |
| `authService.ts` | 认证 API（登录、登出、验证码） |
| `authGuard.ts` | 认证守卫（401 重定向、重定向状态管理） |
| `modelService.ts` | 模型配置 API |

## SSE 流处理模式

`chatService.ts` 中的 `streamSSE()` 统一处理：
1. fetch → 401 检查 → reader 读取
2. SSE 行解析（`data: {...}`）
3. 事件分发到回调（onChunk/onDone/onError/onInterrupted）
4. error 事件携带 `data` 字段传递 Gateway 错误信息

## TTS API 特殊处理

`ttsApi.ts` 使用原生 `fetch`（非 axios），因为响应是 `audio/mpeg` 二进制流。
错误响应解析为 `TTSError`，包含 `code`、`statusCode`、`data`（含 retry_after）。
