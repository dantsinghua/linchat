# Gateway ASR WebSocket + TTS 流式 WebSocket 接口契约

**Date**: 2026-03-02 | **Source**: [docs/linchat-integration-guide.md](../../../docs/linchat-integration-guide.md) 第 6/7/16 节 + [docs/tts-websocket-api.md](../../../docs/tts-websocket-api.md)

## 概述

LinChat 后端通过 frpc-visitor (`127.0.0.1:8100`) 连接 Gateway 的 ASR WebSocket 流式转录接口和 TTS 流式 WebSocket 合成接口。本文档定义接口契约和事件翻译映射。

---

## 1. ASR WebSocket 流式转录

### 连接

```
WS ws://127.0.0.1:8100/v1/audio/transcriptions/stream?api_key={key}
```

### 配置消息

```json
{
  "type": "configure",
  "auto_commit": true,
  "speech_pad_ms": 2000,
  "language": "auto"
}
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `auto_commit` | bool | false | `true`: speech_end 后自动提交转录 |
| `speech_pad_ms` | int | 2000 | 静音填充毫秒数（VAD 灵敏度） |
| `language` | string | "auto" | 语言代码（auto=自动检测） |

### 控制消息

#### commit（手动触发转录）

```json
{"type": "commit"}
```

强制立即触发当前缓冲音频的转录。主要用于：
- `auto_commit=false` 模式下的 Push-to-Talk 场景
- `auto_commit=true` 模式下的超时安全网（`VOICE_MAX_SEGMENT_DURATION` 到期时强制截断）

参考：[integration-guide.md](../../../docs/linchat-integration-guide.md) 第 16 节

### 音频帧

- 格式: PCM16 16kHz mono（二进制帧）
- 发送方式: WebSocket binary message
- 推荐帧大小: 1920 bytes (60ms)

### 事件（Gateway → LinChat）

#### session.created

```json
{
  "type": "session.created",
  "session_id": "asr-session-uuid"
}
```

#### vad.speech_start

```json
{
  "type": "vad.speech_start",
  "timestamp": 1234567890.123
}
```

#### vad.speech_end

```json
{
  "type": "vad.speech_end",
  "timestamp": 1234567890.456,
  "duration_ms": 3200
}
```

#### transcription.completed

```json
{
  "type": "transcription.completed",
  "text": "今天天气怎么样",
  "language": "zh",
  "duration_ms": 3200
}
```

#### transcription.failed

```json
{
  "type": "transcription.failed",
  "error": "ASR model error",
  "code": "ASR_ERROR"
}
```

#### error

```json
{
  "type": "error",
  "message": "Service unavailable",
  "code": "SERVICE_ERROR"
}
```

### 关闭码

| 码 | 含义 | LinChat 处理 |
|----|------|-------------|
| 1000 | 正常关闭 | 正常结束会话 |
| 4002 | ASR 服务不可用 | 立即终止会话，通知前端 |
| 4003 | VAD 服务不可用 | 立即终止会话，通知前端 |
| 其他 | 网络/未知错误 | 立即终止会话，通知前端 |

---

## 2. TTS REST 合成（备选，非推荐）

> **推荐使用第 6 节的 TTS 流式 WebSocket**。REST API 仅作为备选方案保留。

### 请求

```
POST http://127.0.0.1:8100/v1/audio/speech
Authorization: Bearer {api_key}
Content-Type: application/json

{
  "model": "kokoro-tts",
  "input": "需要合成的文本",
  "voice": "zf_xiaobei",
  "response_format": "wav"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 固定 `kokoro-tts` |
| `input` | string | 是 | 合成文本 |
| `voice` | string | 是 | 音色 ID |
| `response_format` | string | 否 | `wav`（默认）/ `mp3` / `opus` |

### 响应

- **成功**: `200 OK`，`Content-Type: audio/wav`，Body 为 WAV PCM16 24kHz mono 音频
- **失败**: `4xx/5xx` JSON 错误

### 超时

- 正常: 30s
- 重试: 不重试（单句失败跳过，不影响其他句子）

---

## 3. 事件翻译映射表

Gateway ASR WebSocket 事件 → 前端 WebSocket 协议事件：

| Gateway ASR 事件 | 前端协议事件 | 字段映射 |
|-----------------|-------------|---------|
| `session.created` | `session.configured` | `session_id` → `session_id`; 追加 `mode`, `status="active"`, `capabilities` |
| `vad.speech_start` | `vad.speech_start` | 注入 Consumer 生成的 `segment_id`; 保留 `timestamp` |
| `vad.speech_end` | `vad.speech_end` | 注入 `segment_id`; 保留 `timestamp`, `duration_ms` |
| `transcription.completed` | `transcription.complete` | 保留 `text`, `language`; 注入 `segment_id`; **触发 VoicePipeline** |
| `transcription.failed` | `transcription.failed` | 保留 `error`, `code`; 注入 `segment_id` |
| `error` | `error` | 映射 `code` → 前端错误码 |

### VoicePipeline 生成的事件（无 Gateway 对应）

| 触发条件 | 前端协议事件 | 字段 |
|---------|-------------|------|
| Agent 开始输出 | `response.start` | `response_id`, `segment_id` |
| Agent 流式内容块 | `response.delta` | `delta.content`, `response_id` |
| Agent 完成输出 | `response.end` | `response_id`, `usage` |
| TTS PCM 音频帧 | WebSocket binary | PCM16 24kHz mono 音频帧（TTSStreamClient._receive_loop → consumer._send_binary） |

---

## 4. 连接生命周期

```
前端 WS connect → VoiceConsumer.connect()
  → ASRStreamClient.connect() → Gateway ASR WS
  → Gateway: session.created → 前端: session.configured

前端 binary(PCM) → VoiceConsumer.receive(bytes)
  → ASRStreamClient.send_audio(PCM) → Gateway ASR WS

Gateway: vad.speech_start → 前端: vad.speech_start
Gateway: vad.speech_end → 前端: vad.speech_end
Gateway: transcription.completed
  → VoicePipeline.run_pipeline(text)
    → TTSStreamClient.connect() → Gateway TTS WS
    → AgentService.execute() → 前端: response.start/delta/end
    → TTSStreamClient.send_text_delta() → Gateway TTS WS → binary(PCM) → 前端
    → TTSStreamClient.send_text_done() → audio.done
    → persist_audio_attachment() → INFO 日志记录

前端 WS close → VoiceConsumer.disconnect()
  → ASRStreamClient.disconnect()
  → 清理 Redis 状态
```

---

## 5. 错误处理

| 场景 | 处理方式 |
|------|---------|
| ASR WS 连接失败 | 拒绝前端 WS 连接，返回错误码 |
| ASR WS 断开 (4002/4003) | 立即终止会话，前端收到 `error` 事件 |
| ASR 转录失败 | 发送 `transcription.failed`，不触发 Pipeline |
| Agent 超时/失败 | 发送 `response.end` + 错误信息，不影响 ASR 连接 |
| TTS WS error 事件 | 跳过该句音频，继续下一句（Gateway 发送 error 事件但不关闭连接） |
| TTS WS 连接失败 | `tts_client=None`，降级为纯文字回复 |
| TTS WS 中途断开 | `_receive_loop` 捕获 ConnectionClosed，设置 done_event 不阻塞 pipeline |
| TTS audio.done 超时 | 30s 后强制关闭 TTS WS，不影响已发送的文字回复 |

---

## 6. TTS 流式 WebSocket（推荐）

> **完整 API 文档**: [docs/tts-websocket-api.md](../../../docs/tts-websocket-api.md)

### 连接

```
WS ws://127.0.0.1:8100/v1/audio/speech/stream?api_key={key}
```

认证通过 query 参数 `api_key`。连接建立后服务端立即校验：
- **有效**: 返回 `session.created` 事件
- **无效/缺失**: 关闭连接，code=`4001`，reason=`"unauthorized"`
- **TTS 服务不可用**: 关闭连接，code=`4002`，reason=`"tts service unavailable"`

### 音频格式

| 属性 | 值 |
|------|-----|
| 编码 | PCM16 (signed 16-bit little-endian) |
| 采样率 | 24000 Hz |
| 声道 | 单声道 (mono) |
| 帧格式 | 原始 PCM bytes（无 WAV 头） |

### 客户端 → 服务端消息（JSON Text 帧）

#### `config` — 配置声音和语速（可选）

```json
{"type": "config", "voice": "zf_xiaobei", "speed": 1.0}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `voice` | string | `"zf_xiaobei"` | 声音 ID |
| `speed` | float | `1.0` | 语速倍率 0.5 ~ 2.0 |

#### `text.delta` — 文本增量输入

```json
{"type": "text.delta", "delta": "你好，"}
```

Agent 每输出一个 content chunk 调用一次。Gateway **自动分句合成**：
- 遇到句尾标点（`。！？；!?;` 或换行）→ 立即切割合成
- 积累超过 30 字符且遇到逗号（`，、,：:`）→ 切割合成
- 积累超过 200 字符 → 强制切割合成

#### `text.done` — 文本输入完毕

```json
{"type": "text.done"}
```

通知服务端所有文本已发送完毕。服务端 flush 剩余缓冲区文本，合成完成后返回 `audio.done`。

### 服务端 → 客户端消息

#### `session.created`（Text 帧）

```json
{"type": "session.created", "session_id": "uuid", "sample_rate": 24000}
```

#### `tts.sentence_start`（Text 帧）

```json
{"type": "tts.sentence_start", "sentence_idx": 0, "text": "你好，世界。"}
```

#### Binary 帧 — PCM16 音频数据

紧随 `tts.sentence_start` 后，服务端发送一个或多个 Binary 帧，内容为原始 PCM16 音频 bytes。

#### `tts.sentence_end`（Text 帧）

```json
{"type": "tts.sentence_end", "sentence_idx": 0}
```

#### `audio.done`（Text 帧）

```json
{"type": "audio.done"}
```

`text.done` 后所有文本合成完毕时发送。客户端收到后可关闭连接。

#### `error`（Text 帧，非致命）

```json
{"type": "error", "message": "TTS 合成失败: ..."}
```

非致命错误，连接不关闭，客户端可继续发送。

### 关闭码

| 码 | 含义 | LinChat 处理 |
|----|------|-------------|
| 1000 | 正常关闭 | 正常完成 |
| 4001 | 认证失败 | tts_client=None，降级纯文字 |
| 4002 | TTS 服务不可用 | tts_client=None，降级纯文字 |

### 生命周期时序图

```
VoicePipeline                       Gateway TTS WS
  │                                    │
  │──── WebSocket Connect ────────────▶│
  │                                    │  验证 api_key
  │◀── session.created ───────────────│
  │     {session_id, sample_rate}      │
  │                                    │
  │──── config ───────────────────────▶│  (可选)
  │     {voice, speed}                 │
  │                                    │
  │──── text.delta "你好，" ──────────▶│  Agent chunk 1
  │──── text.delta "世界。" ──────────▶│  Agent chunk 2
  │                                    │  分句器: "你好，世界。"
  │◀── tts.sentence_start ────────────│
  │◀── Binary PCM ────────────────────│→ consumer._send_binary() → 前端
  │◀── Binary PCM ────────────────────│→ consumer._send_binary() → 前端
  │◀── tts.sentence_end ─────────────│
  │                                    │
  │──── text.done ────────────────────▶│  Agent 完成
  │◀── audio.done ────────────────────│  全部合成完毕
  │                                    │
  │──── Close ────────────────────────▶│
```

### TTSStreamClient 集成要点

| 事件 | TTSStreamClient 处理 | Consumer 动作 |
|------|---------------------|--------------|
| binary PCM | `on_audio` 回调 | `_send_binary(data)` 转发前端 |
| `tts.sentence_start` | `on_sentence_start` 回调（可选日志） | 无 |
| `tts.sentence_end` | 日志记录 | 无 |
| `audio.done` | 设置 `_done_event` | VoicePipeline 可发送 `response.end` |
| `error` | WARNING 日志 | 跳过该句，继续 |
| ConnectionClosed | 设置 `_done_event`，不阻塞 | 降级纯文字 |
