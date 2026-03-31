# LinChat API 参考文档

> 本文档覆盖 LinChat 平台所有 REST API、SSE 事件流和 WebSocket 接口。

---

## 1. 概述

| 项目 | 值 |
|------|-----|
| Base URL | `/api/v1/` |
| 公网地址 | `https://www.greydan.xin/linchat/api/v1` |
| 认证方式 | httpOnly Cookie (`linchat_token`) |
| 内容类型 | `application/json`（除文件上传外） |

### 统一响应格式

```json
{ "code": "SUCCESS", "message": "操作成功", "data": { ... } }
```

失败时 `code` 为具体错误码，`data` 为 `null`。

### 频率限制

| 类别 | 限额 | 类别 | 限额 |
|------|------|------|------|
| 匿名请求 | 100 次/小时 | 认证请求 | 1000 次/小时 |
| LLM 推理 | 60 次/分钟 | 多模态推理 | 1 次/60 秒 |
| 声纹注册 | 5 次/小时 | WS 连接 | 10 次/分钟 |

### 认证机制

- 登录成功后通过 `Set-Cookie` 设置 `linchat_token`（httpOnly, Secure, SameSite=Lax）
- 无操作过期 1 小时（每次请求续期），绝对过期 24 小时（不可续期）
- 部分 GET 接口支持 `?target_user_id=<id>` 代查参数，标注为 **[代查]**

---

## 2. 认证相关

### GET /api/v1/auth/captcha

获取图片验证码（公开接口，验证码有效期 2 分钟）。

**响应 data**: `captcha_id`(string), `image`(base64 PNG)

### POST /api/v1/auth/login

登录（公开接口）。

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `username` | string | 是 | 用户名，最长 50 字符 |
| `password` | string | 是 | SM4 加密密码 |
| `captcha_id` | string | 是 | 验证码标识 |
| `captcha_code` | string | 是 | 4 位验证码 |

**成功响应 data**: `user_id`(int), `username`(string), `expire_time`(ISO 时间)。响应头含 `Set-Cookie`。

**错误**: `AUTH_FAILED`(400) 密码错误 | `CAPTCHA_INVALID`(400) 验证码错误 | `ACCOUNT_LOCKED`(403) 连续 5 次错误锁定 15 分钟 | `USER_DISABLED`(403)

### POST /api/v1/auth/logout

登出，清除 Cookie。**需认证**。

### GET /api/v1/auth/me

当前用户信息。**需认证**。

**响应 data**: `user_id`(int), `username`(string), `type`(`admin`/`user`), `member_type`(`member`/`guest`)

---

## 3. 聊天相关

### POST /api/v1/chat/ — 发送消息 (SSE)

**需认证** | **[代查]**

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `content` | string | 是 | 消息内容，最长 4000 字符 |
| `attachments` | string[] | 否 | 附件 UUID 列表，最多 5 个 |

**响应**: `text/event-stream`，事件类型见 [第 10 节](#10-sse-事件类型)。

携带附件时触发多模态限流（1 次/60 秒），超限返回 `RATE_LIMIT`(429)。

### GET /api/v1/chat/messages/ — 历史消息

**需认证** | **[代查]**

| 查询参数 | 类型 | 默认 | 说明 |
|----------|------|------|------|
| `limit` | int | 50 | 返回条数，1-100 |
| `before_sequence` | int | - | 游标分页：返回 sequence < 此值的消息 |

**响应 data**: `messages`(数组), `has_more`(bool)

**Message 对象字段**: `message_id`, `message_uuid`, `role`(user/assistant/system), `content`, `status`(0=失败/1=正常/2=生成中/3=中断), `sequence`, `created_time`, `request_id`, `model_name`, `response_time_ms`, `attachments`(数组), `is_voice`(bool), `speaker_id`

### GET /api/v1/chat/generating/ — 生成中的消息

**需认证** | **[代查]**。返回当前 status=2 的 assistant 消息，无则 `data.message` 为 `null`。

### POST /api/v1/chat/stop/ — 停止生成

**需认证** | **[代查]**。请求体: `{"request_id": "..."}` 。无任务返回 404。

### POST /api/v1/chat/resume/ — 恢复生成 (SSE)

**需认证** | **[代查]**。请求体: `{"request_id": "..."}`。恢复中断的消息，返回 SSE 流。

### GET /api/v1/chat/reconnect/ — 重连流 (SSE)

**需认证** | **[代查]**。查询参数: `request_id`。断线后获取增量内容。

### POST /api/v1/chat/inference/cancel/ — 取消推理

**需认证**。请求体: `{"request_id": "..."}`。成功返回 `{"cancelled": true, "request_id": "..."}`，无任务返回 404。

---

## 4. 媒体相关

### POST /api/v1/chat/media/upload/ — 上传文件

**需认证** | `multipart/form-data` | 字段: `file`(File)

**文件限制**:

| 类型 | MIME | 大小 | 额外 |
|------|------|------|------|
| 图片 | jpeg/png/gif/webp | 10 MB | - |
| 视频 | mp4/quicktime/webm | 50 MB | 最长 60 秒 |
| 音频 | webm/wav/mpeg | 10 MB | 1-60 秒 |
| 文档 | pdf/docx | 10 MB | - |

**响应 data**: `attachment_uuid`, `media_type`, `mime_type`, `file_name`, `file_size`, `width`, `height`, `duration_seconds`, `is_expired`, `expires_at`, `parsed_at`, `parsed_content_size`, `embedding_status`

> 附件默认 7 天过期，过期后从 MinIO 删除。

### GET /api/v1/chat/media/\<uuid\>/ — 下载文件

**需认证** | **[代查]**。返回 FileResponse。错误: `NOT_FOUND`(404) | `FORBIDDEN`(403) | `ATTACHMENT_EXPIRED`(410)

### POST /api/v1/chat/documents/parse/ — 提交文档解析

**需认证**

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `attachment_uuid` | string | 是 | 已上传文档 UUID |
| `pages` | string | 否 | 页码范围如 `"1-5"` |

已缓存时直接返回 `{"cached": true, "content": "...", "format": "markdown"}`；否则返回 202 含 `task_id`。

### GET /api/v1/chat/documents/tasks/\<task_id\>/ — 任务状态

**需认证**。返回 `task_id`, `status`, `progress`。

### GET /api/v1/chat/documents/tasks/\<task_id\>/result/ — 解析结果

**需认证**。查询参数 `format`(默认 `markdown`，可选 `json`)。返回 `content`, `format`。

---

## 5. 记忆相关

### GET /api/v1/memories/ — 记忆列表

**需认证**

| 查询参数 | 类型 | 默认 | 说明 |
|----------|------|------|------|
| `type` | string | - | `memory`/`compaction`/`daily-summary`/`monthly-summary` |
| `page` | int | 1 | 页码 |
| `page_size` | int | 20 | 每页条数，1-100 |

**响应**: 分页格式 `{items, total, page, page_size}`。

**Memory 对象字段**: `id`, `type`, `name`, `content`, `embedding_status`, `tags`, `created_at`, `updated_at`

### POST /api/v1/memories/ — 创建记忆

**需认证**。请求体: `content`(string, 必填), `name`(string, 可选)。返回 201。

### GET /api/v1/memories/\<id\>/ — 记忆详情

**需认证**。不存在返回 404。

### PUT /api/v1/memories/\<id\>/ — 更新记忆

**需认证**。请求体: `content`(string)。

### DELETE /api/v1/memories/\<id\>/ — 删除记忆

**需认证**。

### POST /api/v1/memories/search/ — 搜索记忆

混合语义搜索：`0.7 * 向量分 + 0.3 * 关键词分`（pgvector + pg_jieba 全文检索）。

**需认证**

| 请求字段 | 类型 | 必填 | 默认 | 说明 |
|----------|------|------|------|------|
| `query` | string | 是 | - | 搜索文本 |
| `limit` | int | 否 | 5 | 结果数量，1-20 |

**响应**: Memory 对象数组，额外包含 `score`(float), `match_type`(`hybrid`/`vector`/`keyword`)。

---

## 6. 模型配置

> 仅 `admin` 用户可访问。

### GET /api/v1/models/ — 模型列表

**需认证(Admin)**

**ModelConfig 对象字段**: `id`, `type`(`tool`/`multimodal`/`embedding`), `name`, `url`, `api_key`(脱敏), `max_context_window`, `max_input_tokens`, `max_output_tokens`, `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `embedding_dimensions`, `is_active`, `effective_context_window`, `created_at`, `updated_at`

### GET /api/v1/models/\<id\>/ — 单个模型

**需认证(Admin)**

### PUT /api/v1/models/\<id\>/ — 更新模型

**需认证(Admin)**

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `name` | string | 是 | 模型名称，最长 100 字符 |
| `url` | string | 是 | API 端点，最长 500 字符 |
| `api_key` | string | 是 | 含 `****` 保留原值；新密钥 >= 12 字符 |
| `max_context_window` | int | 是 | 最大上下文窗口 |
| `max_input_tokens` | int | 是 | 最大输入 Token |
| `max_output_tokens` | int | 是 | 最大输出 Token |
| `temperature` | float | 否 | 0-2 |
| `top_p` | float | 否 | 0-1 |
| `frequency_penalty` | float | 否 | -2 到 2 |
| `presence_penalty` | float | 否 | -2 到 2 |
| `embedding_dimensions` | int | 否 | 仅 embedding 类型 |

> `type` 和 `is_active` 为只读，不可修改。非 embedding 模型设置 `embedding_dimensions` 会校验失败。

---

## 7. 语音相关

### GET /api/v1/voice/speakers/ — 查询声纹

**需认证**。返回声纹信息或 `null`。字段: `id`, `gateway_speaker_id`, `name`, `quality_score`, `enrolled_at`

### POST /api/v1/voice/speakers/ — 注册声纹

**需认证** | `multipart/form-data` | **限流**: 5 次/小时

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 声纹名称，最长 50 字符 |
| `audio` | File | 是 | WAV 格式，最大 10 MB，30-60 秒录音 |

### DELETE /api/v1/voice/speakers/delete/ — 删除声纹

**需认证**。未找到返回 404。

### GET /api/v1/voice/devices/ — 设备列表

**需认证**。字段: `device_uuid`, `name`, `is_active`, `created_at`, `last_active_at`

### POST /api/v1/voice/devices/ — 注册设备

**需认证**。请求体: `name`(string, 最长 100 字符)。返回 201，含明文 API Token（仅一次展示）。

### DELETE /api/v1/voice/devices/\<device_uuid\>/ — 停用设备

**需认证**。不存在返回 404。

### GET /api/v1/voice/settings/ — 语音设置

**需认证**。字段: `wake_words`(string[]), `recording_mode`(`hold`/`toggle`), `vad_sensitivity`(0.0-1.0)

### PUT /api/v1/voice/settings/ — 更新语音设置

**需认证**。部分更新，仅传需修改字段: `wake_words`, `recording_mode`, `vad_sensitivity`。

---

## 8. 成员管理

> 仅 `member_type=member` 的用户可访问（015-family-multiuser）。

### GET /api/v1/members/ — 成员列表

**需认证(member)**。查询参数: `include_expired`(bool, 默认 false)。

**响应 data**: 数组，字段: `user_id`, `username`, `member_type`(`member`/`guest`), `status`, `guest_expires_at`, `is_expired`, `created_time`

### POST /api/v1/members/ — 创建成员

**需认证(member)** | `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 3-50 字符，仅字母/数字/下划线 |
| `password` | string | 是 | SM4 加密密码 |
| `member_type` | string | 是 | `member` / `guest` |
| `audio` | File | 是 | 声纹录音（自动转 WAV） |

**响应 data**: `user_id`, `username`, `member_type`, `status`, `guest_expires_at`

**错误**: `USERNAME_EXISTS`(400) | `VOICEPRINT_FAILED`(400)

---

## 9. WebSocket 接口

### 语音 WebSocket — ws://\<host\>/linchat/ws/voice/

**认证**: Cookie Token 或设备 API Token | **连接限流**: 10 次/分钟/用户

#### 客户端 -> 服务端

| 消息类型 | 格式 | 说明 |
|----------|------|------|
| `config` | JSON | 连接配置: `mode`(`voice_chat`/`ambient`), `sample_rate`(16000), `encoding`(`pcm_s16le`) |
| 音频帧 | 二进制 | PCM 原始音频字节 |
| `response.cancel` | JSON | 取消当前推理和 TTS |

#### 服务端 -> 客户端

| 消息类型 | 格式 | 说明 |
|----------|------|------|
| `vad.speech_start` | JSON | 检测到语音开始 |
| `vad.speech_end` | JSON | 检测到语音结束 |
| `transcription.completed` | JSON | 转录结果: `text`, `is_final` |
| `transcription.failed` | JSON | 转录失败 |
| `response.delta` | JSON | Agent 响应文本增量: `content` |
| TTS 音频帧 | 二进制 | PCM 音频用于播放 |
| `tts.start` / `tts.end` | JSON | TTS 播放开始/结束 |
| `error` | JSON | 错误: `message` |

**Ambient 模式额外事件**:

| 消息类型 | 说明 |
|----------|------|
| `aggregation.utterance_added` | 语句加入聚合缓冲: `text`, `buffer_size` |
| `decision.result` | 决策结果: `decision`(`RESPOND`/`RECORD_ONLY`/`STOP`), `text` |

---

## 10. SSE 事件类型

### 聊天 SSE 流

`POST /chat/`, `POST /chat/resume/`, `GET /chat/reconnect/` 返回的流式事件。

```
data: {"type":"content","content":"你好","message_id":123,"request_id":"abc"}\n\n
```

| type | 说明 |
|------|------|
| `content` | 内容增量（含 `message_id`, 首个 chunk 含 `request_id`） |
| `done` | 生成完成 |
| `error` | 生成错误（`data` 可含 `retry_after`） |
| `interrupted` | 生成被中断 |
| `heartbeat` | 心跳保活（每 15 秒） |

### 全局事件流 — GET /api/v1/events

使用标准 SSE 格式（含 `event:` 字段），30 秒心跳。

| event | 说明 |
|-------|------|
| `message` | 连接建立确认 |
| `heartbeat` | 心跳保活 |
| `logout` | 强制登出: `reason`(`SSO_CONFLICT`/`TOKEN_EXPIRED`/`ADMIN_KICK`) |
| `context_status` | 上下文状态变更（监控面板） |
| `inference_cancel` | 推理取消通知 |
| `doc_parse_progress` | 文档解析进度: `task_id`, `status`, `progress`, `file_name` |

---

## 11. 错误码汇总

### 认证错误

| 错误码 | 状态码 | 说明 |
|--------|--------|------|
| `AUTH_FAILED` | 400 | 用户名或密码错误 |
| `CAPTCHA_INVALID` | 400 | 验证码错误或过期 |
| `TOKEN_EXPIRED` | 401 | Token 过期 |
| `ACCOUNT_LOCKED` | 403 | 账户锁定（含 `remaining_seconds`） |
| `USER_DISABLED` | 403 | 账户被禁用 |

### LLM 错误

| 错误码 | 状态码 | 重试 | 说明 |
|--------|--------|------|------|
| `LLM_CONNECTION_ERROR` | 503 | 3次 | 连接失败 |
| `LLM_TIMEOUT` | 503 | 3次 | 响应超时 |
| `LLM_RATE_LIMIT` | 429 | 否 | 频率限制（含 `retry_after`） |
| `LLM_CONTENT_FILTER` | 400 | 否 | 敏感内容 |
| `LLM_INVALID_RESPONSE` | 503 | 3次 | 无效响应 |
| `LLM_QUOTA_EXCEEDED` | 402 | 否 | 配额用尽 |
| `LLM_CONTEXT_LENGTH` | 400 | 否 | 上下文过长 |

### 业务错误

| 错误码 | 状态码 | 说明 |
|--------|--------|------|
| `MESSAGE_TOO_LONG` | 400 | 消息超长 |
| `EMPTY_MESSAGE` | 400 | 消息为空 |
| `RATE_LIMIT` | 429 | 多模态限流 |
| `ATTACHMENT_EXPIRED` | 410 | 附件过期 |
| `EXTERNAL_SERVICE_ERROR` | 502 | 外部服务异常 |
| `USERNAME_EXISTS` | 400 | 用户名已存在 |
| `VOICEPRINT_FAILED` | 400 | 声纹注册失败 |

---

## 12. 相关文档

- [多模态 API 指南](multimodal-api-guide.md) -- 多模态消息构建
- [TTS WebSocket API](tts-websocket-api.md) -- TTS 流式合成协议
- [Gateway 集成指南](linchat-integration-guide.md) -- LLM Gateway 集成
- [测试指南](testing-guide.md) -- API 测试方法
