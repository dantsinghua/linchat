# WebSocket Protocol Contract: 014-jarvis-ambient-voice

## 概述

本文档定义 ambient 模式对现有 VoiceConsumer WebSocket 协议的增量扩展。所有变更仅影响 `mode=ambient` 的连接，现有 `voice_chat` 和 `continuous_listen` 模式协议完全不变。

## 连接建立

### 端点

```
ws://<host>/ws/voice/
```

复用现有 VoiceConsumer 端点，无新增路由。

### 认证

| 连接来源 | 认证方式 | 说明 |
|----------|----------|------|
| 浏览器 | Session Cookie | 不变 |
| ESP 设备 | `Authorization: Bearer <device_token>` | RegisteredDevice 长效 Token（SM4 加密） |

ESP 设备通过 query 参数或 header 传递 device token，VoiceConsumer 在 `connect()` 中验证 RegisteredDevice。

## 会话配置

### session.configure（客户端 → 服务端）

```json
{
  "type": "session.configure",
  "mode": "ambient",
  "config": {
    "aggregate_timeout": 3.0,
    "max_buffer_size": 10
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mode | string | 是 | `"ambient"` — 新增模式枚举值 |
| config.aggregate_timeout | float | 否 | 覆盖默认聚合超时（秒），默认 `VOICE_AMBIENT_AGGREGATE_TIMEOUT` |
| config.max_buffer_size | int | 否 | 覆盖默认缓冲区上限，默认 `VOICE_AMBIENT_MAX_BUFFER_SIZE` |

### session.configured（服务端 → 客户端）

```json
{
  "type": "session.configured",
  "mode": "ambient",
  "asr_session_id": "gw-xxx-yyy",
  "features": {
    "utterance_aggregation": true,
    "llm_decision": false,
    "cross_device_tts": true
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| features.utterance_aggregation | bool | 话语聚合是否启用（ambient 模式始终 true） |
| features.llm_decision | bool | LLM 意图分类是否启用（取决于 `VOICE_DECISION_USE_LLM`） |
| features.cross_device_tts | bool | 跨设备 TTS 路由是否启用 |

## 音频帧传输

### 客户端 → 服务端

**Binary Frame**：PCM 16-bit LE, 16kHz, mono

与现有协议完全一致。ESP 设备和浏览器使用相同格式。

## 服务端事件

### 聚合相关事件（仅 ambient 模式）

#### aggregation.utterance_added（服务端 → 客户端）

每次 ASR 转录被添加到聚合缓冲区时发送。

```json
{
  "type": "aggregation.utterance_added",
  "text": "帮我开卧室灯",
  "buffer_count": 1,
  "timeout_remaining": 3.0
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| text | string | 本次转录文本 |
| buffer_count | int | 当前缓冲区中话语段数 |
| timeout_remaining | float | 距聚合触发剩余秒数（近似值） |

#### aggregation.completed（服务端 → 客户端）

聚合超时或缓冲区满触发。

```json
{
  "type": "aggregation.completed",
  "text": "帮我开卧室灯 还有空调也开一下",
  "utterance_count": 2,
  "duration": 4.5
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| text | string | 聚合后的完整文本 |
| utterance_count | int | 聚合的话语段数 |
| duration | float | 从第一段到最后一段的时间跨度（秒） |

### 决策相关事件

#### decision.result（服务端 → 客户端）

响应决策引擎完成判定后发送。

```json
{
  "type": "decision.result",
  "decision": "RESPOND",
  "reason": "llm_intent_respond",
  "confidence": 0.85
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| decision | string | `"RESPOND"` / `"RECORD_ONLY"` / `"STOP"` |
| reason | string | 判定原因标识符 |
| confidence | float | 置信度（0.0-1.0，仅 LLM 分类时有意义） |

### 跨设备 TTS 事件

#### tts.audio_frame（服务端 → 非 ESP 客户端）

通过 Django Channels 分组广播的 TTS 音频帧。

**Binary Frame**：PCM 16-bit LE, 24kHz, mono（Gateway TTS 输出格式）

仅发送到同一 user_id 的非 ESP WebSocket 连接。ESP 连接（`_is_device_connection=True`）不接收此消息。

#### tts.started（服务端 → 非 ESP 客户端）

```json
{
  "type": "tts.started",
  "source": "ambient_response",
  "text_preview": "好的，我帮你开卧室灯和空调"
}
```

#### tts.completed（服务端 → 非 ESP 客户端）

```json
{
  "type": "tts.completed",
  "source": "ambient_response"
}
```

### 复用的现有事件

以下事件在 ambient 模式下行为不变：

| 事件 | 方向 | 说明 |
|------|------|------|
| `vad.speech_start` | S→C | VAD 检测到语音开始 |
| `vad.speech_end` | S→C | VAD 检测到语音结束 |
| `transcription.completed` | S→C | ASR 转录完成（在 ambient 模式下额外触发聚合逻辑） |
| `response.delta` | S→C | Agent 流式回复增量（RESPOND 决策后） |
| `response.done` | S→C | Agent 回复完成 |
| `message.saved` | S→C | 消息已保存到数据库 |
| `error` | S→C | 错误通知 |

## 连接生命周期（ambient 模式）

```
ESP 设备连接
    ↓ WebSocket Upgrade + device token 认证
connect() → 加入 voice_tts_{user_id} 分组
    ↓
session.configure { mode: "ambient" }
    ↓
session.configured { features: {...} }
    ↓
[持续运行 — 无空闲超时断开]
    ↓ 发送 PCM 音频帧
    ↓ 接收 Gateway ASR 转录事件
    ↓ UtteranceAggregator 缓冲 + 超时聚合
    ↓ ResponseDecisionService 决策
    ↓ VoicePipeline 处理（如果 RESPOND）
    ↓ TTSRouter 广播到浏览器连接
    ↓
disconnect() → 离开分组 → 清理聚合器
```

### 保活机制

| 层级 | 机制 | 参数 |
|------|------|------|
| WebSocket | ping/pong | 30s interval, 60s timeout |
| ASR | WebSocket ping/pong | 30s interval, 60s timeout |
| Session | Redis TTL 自动续期 | 3600s（每次音频帧刷新） |
| Idle timeout | **禁用** | ambient 模式下不触发空闲断开 |

### ASR 重连

ambient 模式下 ASR WebSocket 意外断开时：

1. 检测断连（`on_close` / `recv` 异常）
2. 等待 2 秒
3. 重建 ASRStreamClient
4. 发送 session.configure 恢复配置
5. 重连成功后恢复音频转发
6. 最多重试 3 次，失败后通知客户端 `error` 事件

## 错误处理

### ambient 模式特有错误

```json
{
  "type": "error",
  "code": "aggregation_overflow",
  "message": "Utterance buffer exceeded maximum size, flushing"
}
```

```json
{
  "type": "error",
  "code": "decision_timeout",
  "message": "LLM intent classification timed out, falling back to rules"
}
```

```json
{
  "type": "error",
  "code": "asr_reconnect_failed",
  "message": "ASR connection lost and reconnection failed after 3 attempts"
}
```

## Django Channels 分组

### 分组命名

```
voice_tts_{user_id}
```

### 分组成员管理

| 事件 | 动作 |
|------|------|
| VoiceConsumer.connect() | `group_add("voice_tts_{user_id}", channel_name)` |
| VoiceConsumer.disconnect() | `group_discard("voice_tts_{user_id}", channel_name)` |

### 分组消息类型

| type | handler | 说明 |
|------|---------|------|
| `tts_audio_frame` | `handle_tts_audio_frame()` | TTS 二进制音频帧 |
| `tts_control` | `handle_tts_control()` | TTS 控制消息（started/completed） |

ESP 连接在 handler 中检查 `_is_device_connection` 并直接 return。
