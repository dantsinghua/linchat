# services 模块指南

## 模块概述

前端 API 服务层，封装所有与后端的 HTTP 通信。基于 Axios 实例统一管理认证、错误拦截和请求配置。

## 文件清单

| 文件 | 用途 |
|------|------|
| `api.ts` | Axios 实例配置（baseURL、Cookie 认证、401/429 拦截器、通用 CRUD 方法） |
| `authService.ts` | 认证 API（登录、登出、获取验证码、获取当前用户） |
| `authGuard.ts` | 全局 401 认证守卫（幂等重定向、防止并发 401 请求风暴） |
| `chatService.ts` | 聊天 API（SSE 流式响应、历史消息分页、停止/恢复/重连生成） |
| `modelService.ts` | 模型配置 API（获取列表、获取单个、更新配置） |
| `mediaApi.ts` | 媒体文件 API（XHR 上传带进度、下载、取消推理、文档解析任务） |
| `voiceApi.ts` | 语音 API（声纹管理、设备管理、语音设置） |

## 关键函数说明

### api.ts

- `get<T>()` / `post<T>()` / `put<T>()` / `del<T>()`: 通用 HTTP 方法，返回 `ApiResponse<T>`
- 请求拦截器: 检测 `isAuthRedirecting()` 状态，重定向中拒绝后续请求
- 响应拦截器: 401 触发 `trigger401Redirect()`，429 打印重试等待时间
- `withCredentials: true`: 自动携带 httpOnly Cookie

### authGuard.ts

- `trigger401Redirect()`: 幂等函数，多次调用只执行一次跳转，防止并发 401 导致的请求风暴
- `isAuthRedirecting()`: 查询当前重定向状态
- `resetAuthGuard()`: 登录成功后重置状态
- 跳转路径: `/linchat/login?redirect=<当前路径>`

### chatService.ts

- `streamSSE()`: 通用 SSE 流处理核心函数，负责 fetch -> 401 检查 -> reader 读取 -> SSE 行解析 -> 回调分发
- 支持的 SSE 事件类型: `content` / `done` / `error` / `interrupted` / `context_compacting` / `context_compacted`
- `sendMessage()`: 发送消息并接收流式响应，支持 `attachments` 附件 UUID 列表
- `resumeGeneration()`: 从中断处恢复生成（status=3）
- `reconnectStream()`: 页面刷新后重连正在生成的消息（status=2）
- `getMessages()`: 获取历史消息，支持游标分页（`beforeSequence`）
- `getGeneratingMessage()`: 检测是否有正在生成中的消息
- `stopGeneration()`: 停止生成

### mediaApi.ts

- `uploadMedia()`: 使用 XMLHttpRequest（非 fetch/axios）实现上传进度监控，返回 `MediaUploadResponse`
- `getMediaUrl()`: 构造媒体文件访问 URL
- `downloadMedia()`: 下载媒体文件为 Blob
- `cancelInference()`: 取消多模态推理任务
- `createDocParseTask()` / `getDocParseResult()`: 文档解析任务的创建和结果获取

### modelService.ts

- `fetchModels()`: 获取所有模型配置列表
- `fetchModelById()`: 获取单个模型配置
- `updateModel()`: 更新模型配置（PUT 请求）

### voiceApi.ts

- `getSpeakerProfile()`: GET /voice/speakers/ -> SpeakerProfile
- `enrollSpeaker(name, audioBlob)`: POST /voice/speakers/ (multipart) -> SpeakerProfile
- `deleteSpeaker()`: DELETE /voice/speakers/delete/ -> null
- `getDevices()`: GET /voice/devices/ -> RegisteredDevice[]
- `registerDevice(data)`: POST /voice/devices/ -> DeviceRegisterResponse（含一次性 apiToken）
- `deleteDevice(uuid)`: DELETE /voice/devices/{uuid}/ -> null
- `getVoiceSettings()`: GET /voice/settings/ -> VoiceSettings
- `updateVoiceSettings(data)`: PUT /voice/settings/ -> VoiceSettings
- 内置 snake_case <-> camelCase 格式转换

### authService.ts

- `login()`: 登录，密码通过 SM4 加密后传输
- `logout()`: 登出，忽略后端错误
- `getCaptcha()`: 获取验证码（captcha_id + base64 图片）
- `getCurrentUser()`: 获取当前用户信息

## 数据流

```
组件/Hook
  ↓ 调用
services (api.ts / chatService.ts / mediaApi.ts / ...)
  ↓ HTTP/SSE
后端 API (/api/v1/*)
```

## 依赖关系

- `api.ts` 被 `authService.ts`、`chatService.ts`、`modelService.ts`、`mediaApi.ts` 依赖
- `authGuard.ts` 被 `api.ts`（拦截器）和 `chatService.ts`（SSE 流）依赖
- 所有服务依赖 `@/types` 中的类型定义

## 测试方法

- 单元测试: mock Axios 实例和 fetch，验证请求参数和响应处理
- SSE 流测试: mock ReadableStream，验证事件解析和回调分发
- 认证守卫测试: 验证幂等性、并发安全和重定向路径
