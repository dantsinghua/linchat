# llmgateway Integration Reference

**Feature Branch**: `009-voice-interaction`
**Date**: 2026-02-14
**Updated**: 2026-02-24 (对齐 llmgateway v2.0.0 实际 API)

## 概述

LinChat 作为 llmgateway 的代理层，对接两类接口：
- **WebSocket**: 实时语音流（VAD + 声纹识别 + 模型推理）
- **HTTP REST**: 声纹注册/管理 + 异步 STT 转写

> **重要变更**: llmgateway WebSocket 不提供独立 STT 转写事件。STT 转写由 LinChat 自行通过 HTTP 异步完成。

## WebSocket 上游连接

### 端点
```
ws://{LLM_GATEWAY_WS_URL}/v1/voice/stream?api_key={LLM_GATEWAY_WS_API_KEY}
```

### LinChat 代理行为

| llmgateway 事件 | LinChat 处理 | 转发给客户端 |
|-----------------|-------------|-------------|
| `session.created` | 记录 session_id，发送 `session.configure` | 不转发 |
| `session.configured` | 记录状态（data 仅含 `{status: "ok"}`） | 转发 |
| `vad.speech_start` | 开始缓存音频帧 | 转发 |
| `vad.speech_end` | 停止缓存，**触发异步 STT 转写**（见下方说明） | 转发 |
| `speaker.identified` | 解析 `identified` 布尔字段，查声纹匹配表→找 user_id | 转发（增加 user_id, user_name） |
| `response.start` | 创建 assistant 消息（status=生成中），记录 `response_id` | 转发 |
| `response.delta` | 从 `data.delta.content` 累积 AI 回复文本 | 转发 |
| `response.end` | 完成 assistant 消息 + 保存音频到 MinIO（使用 `response_id` 匹配，usage 含 `input_tokens`/`output_tokens`/`audio_duration_ms`） | 转发 + 发 message.saved |
| `error` | 记录日志，映射错误码 | 转发 |

> **注意**: `response.cancel` 发送后 llmgateway **不发送** `response.end`，LinChat 需主动清理状态并标记回复为 interrupted。需跟踪 `response_id` 用于 cancel 匹配。

### STT 转写（LinChat 自行实现）

llmgateway WebSocket **不提供**独立 STT 转写事件（无 `transcription.delta` / `transcription.complete`）。LinChat 在 `vad.speech_end` 后自行实现异步转写：

1. `vad.speech_end` 触发 → LinChat 将缓存的音频帧合并为 WAV 文件（添加 44-byte WAV 头）
2. 异步调用 HTTP `POST /v1/chat/completions`：
   ```json
   {
     "model": "minicpm-o",
     "messages": [{
       "role": "user",
       "content": [
         {"type": "text", "text": "请逐字转写以下音频内容，只输出转写文字"},
         {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,<base64 WAV>"}}
       ]
     }],
     "stream": false
   }
   ```
3. 转写完成后更新 `Message.content` 字段
4. 通过 WebSocket 发送 LinChat 自行生成的 `transcription.complete` 事件到客户端

此过程与 WebSocket 主流程**并行执行**，不阻塞 AI 回复流。

### session.configure 参数映射

| LinChat 模式 | llmgateway 参数 |
|-------------|----------------|
| voice_chat (P1) | `vad_enabled=true, auto_respond=true, speaker_identify=false, audio_output=false` |
| continuous_listen (P5) | `vad_enabled=true, auto_respond=false, speaker_identify=true, audio_output=false` |

**说明**:
- `audio_output=false`: 本版本不需要 TTS 音频回复
- `tool_calling_model`: 可选参数，传入远程 Tool Calling 模型名称（如 "gpt-4o"），null 不启用
- P5 模式 `auto_respond=false`: LinChat 收到异步 STT 转写结果后先执行响应决策，决定 RESPOND 才发 `input.commit`
- **发送 `session.configure` 会清空 llmgateway 端的对话历史**

## HTTP REST 上游接口

**Base URL**: `{LLM_GATEWAY_HTTP_URL}`（环境变量，默认 `http://127.0.0.1:8889`。⚠️ 不可使用 8081，已被 Langfuse Nginx 占用）

**认证**: 所有 HTTP 请求 MUST 携带 `Authorization: Bearer {LLM_GATEWAY_WS_API_KEY}` 头（与 WebSocket 端点共用同一密钥）

**音频格式**: 所有 HTTP 端点仅接受 **WAV (PCM16, 16kHz, mono)**，其他格式返回 `400 E6001 AUDIO_FORMAT_INVALID`。

**传输方式**: 所有 HTTP 端点同时支持两种音频输入方式：
- **JSON body**: `{"audio": "<base64 编码的 WAV 文件全部字节>"}`
- **multipart/form-data**: `audio=<文件字段>`

### 声纹注册

```
POST {LLM_GATEWAY_HTTP_URL}/v1/voice/speakers
Authorization: Bearer {api_key}
Content-Type: multipart/form-data

audio: <WAV binary>
```

也支持 JSON body:
```json
{
  "audio": "<base64 WAV>",
  "speaker_id": null
}
```

**Response** (201 Created / 200 OK):
```json
{
  "speaker_id": "spk_xxxx",
  "quality_score": 0.85,
  "created": true
}
```

**Response 字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `speaker_id` | string | llmgateway 分配的声纹用户唯一标识 |
| `quality_score` | float | 声纹质量评分（0.0-1.0），反映录音环境和声纹清晰度。建议 ≥ 0.6 为可用，< 0.6 提示用户重新录制 |
| `created` | bool | true=新建声纹，false=更新已有声纹 |

**Error** (400):
```json
{
  "error": {
    "code": "E6002",
    "type": "validation_error",
    "message": "音频时长不在 10-30 秒范围"
  }
}
```

### 声纹匹配（独立 HTTP 端点）

```
POST {LLM_GATEWAY_HTTP_URL}/v1/voice/speakers/identify
Authorization: Bearer {api_key}
Content-Type: application/json

{"audio": "<base64 WAV>", "threshold": 0.6}
```

**Response** (200):
```json
{
  "identified": true,
  "speaker_id": "spk_f7e2a1b3",
  "confidence": 0.82
}
```

未匹配时:
```json
{
  "identified": false,
  "speaker_id": null,
  "confidence": 0.31
}
```

### 声纹列表

```
GET {LLM_GATEWAY_HTTP_URL}/v1/voice/speakers
```

**Response** (200):
```json
{
  "speakers": [
    {
      "speaker_id": "spk_f7e2a1b3",
      "quality_score": 0.85,
      "created_at": "2026-02-14T10:30:00Z",
      "updated_at": "2026-02-14T10:30:00Z",
      "expires_at": "2027-02-14T10:30:00Z"
    }
  ],
  "total": 1
}
```

### 声纹删除

```
DELETE {LLM_GATEWAY_HTTP_URL}/v1/voice/speakers/{speaker_id}
```

**Response**: `204 No Content`

### 语音聊天（STT 转写复用此端点）

```
POST {LLM_GATEWAY_HTTP_URL}/v1/chat/completions
Authorization: Bearer {api_key}
Content-Type: application/json
```

LinChat 用于异步 STT 转写时的请求：
```json
{
  "model": "minicpm-o",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "请逐字转写以下音频内容，只输出转写文字"},
      {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,<base64 WAV>"}}
    ]
  }],
  "stream": false
}
```

音频必须为 WAV (PCM16, 16kHz, mono)，通过 data URL 格式传递。

## 错误处理映射

### WebSocket 错误

| llmgateway 错误 | LinChat 处理 |
|-----------------|-------------|
| WebSocket 连接失败 | 自动重连一次，失败后通知客户端 `GATEWAY_UNAVAILABLE` |
| `error.recoverable=true` | 保持连接，等待恢复，通知客户端 |
| `error.recoverable=false` | 断开上游连接，尝试重建，通知客户端 |

### HTTP 错误

| llmgateway 错误 | LinChat 处理 |
|-----------------|-------------|
| HTTP 声纹注册/匹配 4xx | 转换为友好错误消息返回给前端 |
| HTTP 声纹注册/匹配 5xx | 重试一次，失败返回 `ExternalServiceError` |
| HTTP STT 转写失败 | 记录日志，Message.content 保持为空，不影响 AI 回复流 |

### llmgateway 错误码体系

| 错误码 | HTTP | 说明 | recoverable (WS) |
|--------|:----:|------|:-----------------:|
| `E6001` | 400 | 音频格式不支持（非 WAV PCM16 16kHz mono） | false |
| `E6002` | 400 | 音频超限（>10MB / >60s / 注册不在 10-30s） | false |
| `E6003` | 404 | 声纹不存在 | false |
| `E6004` | 503 | 语音服务不可用（VAD/SpeechBrain 模型加载失败） | true |
| `E2001` | 401 | API Key 无效或缺失 | false |
| `E3001` | 404 | 模型不存在 | false |
| `E3002` | 503 | 模型不可用（未加载/加载中） | true |
| `E3003` | 504 | 模型超时 | true |
