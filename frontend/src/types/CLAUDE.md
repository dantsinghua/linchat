# types 模块指南

## 模块概述

前端 TypeScript 类型定义，包含 API 响应、聊天消息、媒体文件、模型配置和国密算法库的类型声明。

## 文件清单

| 文件 | 用途 |
|------|------|
| `index.ts` | 核心类型定义（API 响应、用户、消息、SSE 事件、上下文监控、错误） |
| `media.ts` | 媒体类型定义（附件、上传任务、限制常量、工具函数） |
| `model.ts` | 模型配置类型定义（GET 响应 camelCase、PUT 请求 snake_case） |
| `sm-crypto.d.ts` | sm-crypto 国密算法库类型声明（SM2/SM3/SM4） |
| `voice.ts` | 语音类型定义（会话状态、录音模式、声纹档案、设备、设置、WebSocket 事件） |

## 关键类型说明

### index.ts

**API 基础类型:**
- `ApiResponse<T>`: 统一 API 响应格式 `{ code, message, data }`
- `ApiError`: 错误响应 `{ code, message, retry_after?, remaining_seconds? }`

**用户类型:**
- `User`: `{ user_id, username }`
- `LoginRequest`: 登录请求参数
- `CaptchaResponse`: 验证码响应 `{ captcha_id, captcha_image }`

**消息类型:**
- `Message`: 聊天消息实体
  - `status`: `0`=失败 / `1`=正常 / `2`=生成中 / `3`=中断
  - `attachments?`: 多模态消息的媒体附件列表（`MediaAttachment[]`）
  - `request_id?`: 用于停止/恢复/重连生成
- `MessageRole`: `'user' | 'assistant' | 'system'`
- `MessageStatus`: `0 | 1 | 2 | 3`

**SSE 事件类型:**
- `ChatStreamEvent`: 流式响应事件
  - `type`: `content` / `done` / `error` / `interrupted` / `context_compacting` / `context_compacted`
  - `data?`: 扩展数据字段
    - `gateway_error?`: E3001（模型不存在）/ E3002（多模态服务不可用）
    - `retry_after?`: E3002 模型切换等待秒数
    - `content_control?`: 安全护栏触发标志

**上下文监控类型:**
- `TokenBreakdown`: Token 分布明细（system_prompt/history/memories/compaction/tool_defs/tool_calls/tool_results/user_input/total）
- `AlertLevel`: `'normal' | 'warning' | 'critical'`
- `MonitorData`: 监控面板数据（模型信息、token 用量、上下文分布、记忆、工具调用）
- `ContextStatus`: MonitorData 的 SSE 事件版本（携带 `type: 'context_status'`）

**历史消息类型:**
- `HistoryResponse`: `{ messages, has_more }` -- 支持游标分页
- `GeneratingResponse`: `{ message }` -- 检测生成中消息

### media.ts

**核心类型:**
- `MediaType`: `'image' | 'video' | 'audio' | 'document'`
- `MediaAttachment`: 媒体附件元数据（uuid、media_type、mime_type、file_name、file_size、width/height、duration_seconds、expires_at、is_expired）
- `MediaUploadResponse`: 上传响应 `{ code, message, data: MediaAttachment }`
- `UploadProgress`: 上传进度 `{ percent, stage, status }`
- `UploadTask`: 上传任务状态（id、file、previewUrl、progress、status、error、attachment）
- `InferenceCancelResponse`: 推理取消响应

**限制常量 `MEDIA_LIMITS`:**
- 图片: 最大 10MB，支持 JPEG/PNG/GIF/WebP
- 视频: 最大 50MB，支持 MP4/MOV/WebM
- 音频: 最大 10MB，支持 WebM/WAV/MP3
- 文档: 最大 10MB，支持 PDF/DOCX
- 时长: 最长 60 秒（音频/视频）
- 附件数: 单次最多 5 个

**工具函数:**
- `getMediaTypeFromMime(mimeType)`: MIME 类型 -> MediaType 映射
- `getFileSizeLimit(mediaType)`: 获取文件大小限制
- `formatFileSize(bytes)`: 格式化文件大小显示
- `formatDuration(seconds)`: 格式化时长显示 `M:SS`

### model.ts

- `ModelType`: `'tool' | 'multimodal' | 'embedding'`
- `ModelConfig`: GET 响应（camelCase 字段名：maxContextWindow、maxInputTokens 等），包含 effectiveContextWindow 计算字段
- `ModelUpdateRequest`: PUT 请求（snake_case 字段名：max_context_window、max_input_tokens 等），选填字段用 `number | null`

### sm-crypto.d.ts

为 `sm-crypto` 国密算法库提供 TypeScript 类型声明：
- `SM4`: 对称加密（ECB/CBC 模式），用于密码和 API Key 加密
- `SM3`: 哈希算法
- `SM2`: 非对称加密/签名

### voice.ts

**关键类型:**

- `VoiceSessionState`: 8 态枚举（idle/configuring/listening/recording/processing/responding/interrupted/error）
- `RecordingMode`: 'hold' | 'toggle'
- `SpeakerProfile`: 声纹档案（id, gatewaySpeakerId, name, qualityScore, enrolledAt）
- `RegisteredDevice`: 注册设备（deviceUuid, name, isActive, createdAt, lastActiveAt）
- `VoiceSettings`: 语音设置（wakeWords, recordingMode, vadSensitivity）
- `VoiceWSEventType`: 14 种 WebSocket 事件类型
- `VoiceResponseDelta`: 流式回复增量
- `VoiceMessageSaved`: 消息持久化确认

## 依赖关系

- `index.ts` 被 services、stores、hooks、components 广泛引用
- `media.ts` 被 `chatStore`、`uploadStore`、`mediaApi`、媒体相关组件引用
- `model.ts` 被 `modelStore`、`modelService`、settings 组件引用
- `sm-crypto.d.ts` 被 `@/utils/crypto` 引用
- `voice.ts` 被 `voiceStore`、`voiceApi`、voice hooks、voice 组件引用

## 测试方法

- 类型文件本身不需要运行时测试
- 通过 TypeScript 编译器（`tsc --noEmit`）验证类型正确性
- 工具函数（getMediaTypeFromMime、formatFileSize 等）可通过单元测试验证
