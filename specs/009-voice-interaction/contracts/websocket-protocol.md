# WebSocket Protocol: LinChat ↔ Client

**Feature Branch**: `009-voice-interaction`
**Date**: 2026-02-14
**Updated**: 2026-02-24 (对齐 llmgateway v2.0.0 实际 API)

## 端点

```
ws://{host}/ws/voice/?token={api_token}          # 后端内部路径（Django Channels 路由）
ws://{host}/linchat/ws/voice/?token={api_token}   # 前端通过 Nginx 访问的路径
```

> Nginx 将 `/linchat/ws/` 代理到后端 `/ws/`，前端需使用含 `/linchat` 前缀的路径。

**认证**:
- Web 端: httpOnly Cookie 自动携带（无需 query 参数）
- 外部设备: `token` query 参数携带设备 API Token

## 帧类型

| 方向 | 帧类型 | 内容 |
|------|--------|------|
| Client → Server | Binary | PCM16 音频数据（16kHz, 16bit, mono, 960 bytes/帧 = 30ms） |
| Client → Server | Text (JSON) | 控制消息 |
| Server → Client | Text (JSON) | 事件通知 |

## JSON 消息结构

```json
{
  "type": "message_type",
  "event_id": "evt_xxxx",
  "data": {}
}
```

## Client → Server 控制消息

### session.configure

进入语音模式时发送，配置会话参数。

```json
{
  "type": "session.configure",
  "data": {
    "mode": "voice_chat",
    "vad_enabled": true,
    "vad_threshold": 0.5,
    "speaker_identify": false,
    "auto_respond": true,
    "recording_mode": "toggle",
    "tool_calling_model": null
  }
}
```

**mode 取值**:
- `voice_chat`: 语音模式（P1），auto_respond=true
- `continuous_listen`: 持续监听（P5），speaker_identify=true

> **注意**: 发送 `session.configure` 会**清空 llmgateway 端的对话历史**。如需运行时动态调参且不清空历史，使用 `session.update`。

**参数说明**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| mode | string | "voice_chat" | LinChat 会话模式（voice_chat / continuous_listen） |
| vad_enabled | bool | true | 启用 VAD 检测 |
| vad_threshold | float | 0.5 | VAD 语音概率阈值 |
| speaker_identify | bool | false | 是否对每段语音做声纹匹配 |
| speaker_threshold | float | 0.6 | 声纹匹配置信度阈值 |
| auto_respond | bool | true | 语音段结束后自动送入模型推理 |
| audio_output | bool | false | 是否请求音频回复 |
| model | string | "minicpm-o" | 推理模型 |
| tool_calling_model | string\|null | null | 远程 Tool Calling 模型（如 "gpt-4o"），null 不启用 |
| chunk_duration_ms | int | 30 | 客户端每帧时长（毫秒） |
| recording_mode | string | "toggle" | 录音交互模式（hold/toggle），LinChat 前端专用 |

### input.commit

手动触发推理（auto_respond=false 模式下使用）。

```json
{
  "type": "input.commit",
  "event_id": "evt_001"
}
```

### response.cancel

打断正在进行的推理。**必须携带 `response_id`**，取消后 llmgateway **不发送** `response.end`，LinChat 需主动清理状态并标记回复为 interrupted。

```json
{
  "type": "response.cancel",
  "data": {
    "response_id": "resp_x1y2z3"
  }
}
```

### session.update

运行时动态调参（不清空 llmgateway 对话历史）。适用于用户在语音会话进行中修改设置（如 VAD 灵敏度、唤醒词）。

```json
{
  "type": "session.update",
  "event_id": "evt_002a",
  "data": {
    "vad_threshold": 0.7,
    "speaker_threshold": 0.5
  }
}
```

**可更新参数**: `vad_threshold`、`speaker_threshold`、`vad_enabled`、`speaker_identify`。其余参数（mode、auto_respond、model）变更需重新 `session.configure`。

> **与 session.configure 的区别**: `session.update` 仅更新指定参数，不清空 llmgateway 对话历史；`session.configure` 重置全部参数并清空历史。

### session.close

主动关闭语音会话。

```json
{
  "type": "session.close",
  "event_id": "evt_003"
}
```

## Server → Client 事件

### session.created

连接建立后 llmgateway **立即**发送，包含会话基本参数。LinChat 不转发此事件给客户端（内部消费）。

```json
{
  "type": "session.created",
  "event_id": "evt_001",
  "data": {
    "session_id": "sess_a1b2c3d4",
    "sample_rate": 16000,
    "channels": 1,
    "encoding": "pcm16",
    "server_time": "2026-02-24T10:30:00Z"
  }
}
```

### session.configured

会话配置完成，可以开始发送音频。

```json
{
  "type": "session.configured",
  "event_id": "evt_100",
  "data": {
    "status": "ok"
  }
}
```

> **注**: `session_id` 在 `session.created` 中已返回，`user_id` 由 LinChat 认证层获取，不由 llmgateway 提供。

### vad.speech_start

检测到用户开始说话。

```json
{
  "type": "vad.speech_start",
  "event_id": "evt_101",
  "data": {
    "timestamp_ms": 1500,
    "speech_prob": 0.87
  }
}
```

### vad.speech_end

检测到用户停止说话。LinChat 在收到此事件后，将缓存的音频帧异步发送给 HTTP `POST /v1/chat/completions` 进行 STT 转写。

```json
{
  "type": "vad.speech_end",
  "event_id": "evt_102",
  "data": {
    "timestamp_ms": 4200,
    "duration_ms": 2700,
    "speech_prob": 0.12
  }
}
```

### speaker.identified

声纹识别结果（仅 continuous_listen 模式，`speaker_identify=true` 时触发）。

**匹配到已注册说话人**:

```json
{
  "type": "speaker.identified",
  "event_id": "evt_103",
  "data": {
    "identified": true,
    "speaker_id": "spk_xxxx",
    "confidence": 0.82,
    "user_id": 1,
    "user_name": "爸爸"
  }
}
```

**未匹配到**:

```json
{
  "type": "speaker.identified",
  "event_id": "evt_103",
  "data": {
    "identified": false,
    "speaker_id": null,
    "confidence": 0.31
  }
}
```

**LinChat 增强**: llmgateway 原始事件仅包含 `identified`/`speaker_id`/`confidence`，LinChat 在转发时通过声纹匹配表查询增加 `user_id` 和 `user_name`。

### STT 转写（LinChat 自行实现，非 llmgateway 事件）

> llmgateway WebSocket **不提供**独立的 STT 转写事件（如 `transcription.delta` / `transcription.complete`）。
>
> LinChat 在 `vad.speech_end` 后，将缓存的音频段通过 HTTP `POST /v1/chat/completions` 发送给 MiniCPM-o（prompt="请逐字转写以下音频内容，只输出转写文字"），异步获取转写文本存入 Message.content。此过程与 WebSocket 主流程并行执行，不阻塞 AI 回复流。
>
> 转写完成后，LinChat 向客户端发送自行生成的 `transcription.complete` 事件：

```json
{
  "type": "transcription.complete",
  "event_id": "evt_105",
  "data": {
    "text": "今天天气怎么样",
    "message_id": 1001
  }
}
```

> 此事件由 **LinChat 生成**，不来自 llmgateway。客户端收到后更新对应语音消息的 content 显示。

**转写失败时**，LinChat 向客户端发送 `transcription.failed` 事件：

```json
{
  "type": "transcription.failed",
  "event_id": "evt_105b",
  "data": {
    "message_id": 1001,
    "error": "STT 转写超时"
  }
}
```

> 客户端收到后显示"语音转写失败"标签，音频附件仍可正常播放。Message.content 保持为空字符串。

### response.start

AI 开始生成回复。

```json
{
  "type": "response.start",
  "event_id": "evt_106",
  "data": {
    "response_id": "resp_x1y2z3",
    "model": "minicpm-o",
    "speaker_id": "spk_f7e2a1b3"
  }
}
```

| 字段 | 说明 |
|------|------|
| `response_id` | 本次推理的唯一 ID（用于 cancel） |
| `model` | 使用的推理模型 |
| `speaker_id` | 识别到的说话人（未识别或未启用时为 null） |

### response.delta

AI 回复流式文本片段。

```json
{
  "type": "response.delta",
  "event_id": "evt_107",
  "data": {
    "response_id": "resp_x1y2z3",
    "delta": {
      "content": "今天",
      "audio": null
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `response_id` | string | 关联的推理 ID |
| `delta.content` | string\|null | 文本增量 |
| `delta.audio` | string\|null | base64 PCM16 音频块（当前版本为 null） |

### response.end

AI 回复完成。

```json
{
  "type": "response.end",
  "event_id": "evt_108",
  "data": {
    "response_id": "resp_x1y2z3",
    "usage": {
      "input_tokens": 150,
      "output_tokens": 45,
      "audio_duration_ms": 0
    }
  }
}
```

> **注意**: 当推理被 `response.cancel` 取消后，llmgateway **不发送** `response.end`。LinChat 需自行检测取消状态并完成清理。

### message.saved

LinChat 特有事件：消息已持久化到数据库。

```json
{
  "type": "message.saved",
  "event_id": "evt_109",
  "data": {
    "user_message_id": 1001,
    "assistant_message_id": 1002,
    "user_message_uuid": "uuid-xxxx",
    "assistant_message_uuid": "uuid-yyyy"
  }
}
```

### decision.result

响应决策结果（仅 continuous_listen 模式）。

```json
{
  "type": "decision.result",
  "event_id": "evt_110",
  "data": {
    "action": "RESPOND",
    "reason": "wake_word_detected",
    "wake_word": "小鱼"
  }
}
```

**action 取值**:
- `RESPOND`: 触发 AI 回复
- `RECORD_ONLY`: 仅记录，不回复
- `STOP`: 停止当前操作

### error

错误事件。

```json
{
  "type": "error",
  "event_id": "evt_111",
  "data": {
    "code": "GATEWAY_UNAVAILABLE",
    "message": "llmgateway WebSocket 连接失败",
    "recoverable": true
  }
}
```

**错误码**:
| code | 说明 | recoverable |
|------|------|-------------|
| `AUTH_FAILED` | 认证失败 | false |
| `SESSION_CONFLICT` | 已有活跃语音会话 | false |
| `GATEWAY_UNAVAILABLE` | llmgateway 不可用 | true |
| `GATEWAY_ERROR` | llmgateway 返回错误 | depends |
| `RECORDING_TOO_LONG` | 录音超时 | true |
| `SPEAKER_NOT_FOUND` | 声纹未匹配 | true |

**llmgateway 错误码映射**（来自 llmgateway 的错误在 LinChat 中转换为 GATEWAY_ERROR）:
| llmgateway 码 | 说明 | recoverable |
|---------------|------|-------------|
| `E6001` | 音频格式不支持 | false |
| `E6002` | 音频超限 | false |
| `E6003` | 声纹不存在 | false |
| `E6004` | 语音服务不可用 | true |
| `E2001` | API Key 无效 | false |
| `E3001` | 模型不存在 | false |
| `E3002` | 模型不可用 | true |
| `E3003` | 模型超时 | true |

## 连接生命周期

```
Client                    LinChat                   llmgateway
  │                         │                          │
  │──── WS Connect ────────►│                          │
  │                         │── Auth Check ──►         │
  │◄─── Connected ──────────│                          │
  │                         │                          │
  │── session.configure ───►│                          │
  │                         │── WS Connect ──────────►│
  │                         │◄── session.created ──────│  (LinChat 内部消费)
  │                         │── session.configure ────►│  (清空 llmgateway 对话历史)
  │                         │◄── session.configured ───│  (data: {status: "ok"})
  │◄── session.configured ──│                          │
  │                         │                          │
  │── Binary (PCM16) ──────►│── Binary (PCM16) ──────►│
  │── Binary (PCM16) ──────►│── Binary (PCM16) ──────►│
  │                         │                          │
  │                         │◄── vad.speech_end ───────│
  │◄── vad.speech_end ──────│                          │
  │                         │── 异步 HTTP STT 转写 ──►│  (POST /v1/chat/completions)
  │                         │◄── response.start ───────│  (含 response_id, model, speaker_id)
  │◄── response.start ──────│                          │
  │                         │◄── response.delta ───────│  (含 response_id, delta.content)
  │◄── response.delta ──────│                          │
  │                         │◄── response.end ─────────│  (含 response_id, usage: input_tokens/output_tokens)
  │◄── response.end ────────│                          │
  │                         │── Save Messages ──►DB    │
  │◄── message.saved ───────│                          │
  │                         │◄── STT 转写完成 ─────────│  (HTTP 响应)
  │◄── transcription.complete│  (LinChat 自行生成)      │
  │                         │                          │
  │── session.close ───────►│                          │
  │                         │── WS Close ─────────────►│
  │◄── WS Close ────────────│                          │
```

> **对话历史**: llmgateway WebSocket 会话维护最近 **5 条问答**的对话历史，超出自动移除最早条目。`session.configure` 清空历史，`session.update` 不清空。

## 心跳

- WebSocket 原生 Ping/Pong
- **客户端**每 30 秒发送 Ping
- **服务端**自动回复 Pong（WebSocket 协议）
- 服务端 60 秒未收到客户端 Ping 则断开连接
