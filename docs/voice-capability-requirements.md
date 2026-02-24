# LLM Gateway 语音能力 — 接口说明与对接文档

**版本**: 2.0.0 | **日期**: 2026-02-24 | **特性分支**: `006-voice-capability`

---

## 目录

1. [概述](#1-概述)
2. [通用约定](#2-通用约定)
3. [HTTP 端点：VAD 语音活动检测](#3-http-端点vad-语音活动检测)
4. [HTTP 端点：声纹管理](#4-http-端点声纹管理)
5. [HTTP 端点：语音聊天（MiniCPM-o 音频交互）](#5-http-端点语音聊天minicpm-o-音频交互)
6. [WebSocket 端点：持续监控模式](#6-websocket-端点持续监控模式)
7. [错误码参考](#7-错误码参考)
8. [对接示例](#8-对接示例)
9. [FAQ](#9-faq)

---

## 1. 概述

### 1.1 能力总览

LLM Gateway 语音扩展为上游应用提供以下能力：

| 能力 | 端点 | 说明 |
|------|------|------|
| **VAD 语音活动检测** | `POST /v1/voice/vad` | 判断音频是否包含人声，返回语音段起止时间 |
| **声纹注册** | `POST /v1/voice/speakers` | 发送音频，网关提取 embedding 并返回 speaker_id |
| **声纹匹配** | `POST /v1/voice/speakers/identify` | 发送音频，网关与已有声纹比对并返回 speaker_id |
| **声纹列表** | `GET /v1/voice/speakers` | 查询所有已注册声纹 |
| **声纹删除** | `DELETE /v1/voice/speakers/{speaker_id}` | 物理删除声纹数据 |
| **语音聊天** | `POST /v1/chat/completions` | 现有聊天接口扩展，支持音频输入 |
| **持续监控** | `WS /v1/voice/stream` | WebSocket 长连接，服务端自动编排 VAD → 声纹 → 推理 |

### 1.2 架构概要

```
上游应用（linchat / Web / 树莓派）
  │
  ├── HTTP 请求（一次性操作）
  │     ├── VAD 检测
  │     ├── 声纹注册 / 匹配 / 管理
  │     └── 语音聊天（MiniCPM-o 端到端）
  │
  └── WebSocket 长连接（持续监控）
        └── 实时音频流 → 服务端自动编排
              VAD → 声纹识别 → MiniCPM-o 推理 → 事件推送

网关内部：
  ┌─────────────────────────────────────────┐
  │  Silero VAD (CPU, <2MB, 常驻内存)        │
  │  SpeechBrain ECAPA-TDNN (CPU, ~100MB)   │
  │  MiniCPM-o 4.5 (GPU, ~20GB, vLLM)      │
  │  PostgreSQL + pgvector (声纹存储)        │
  │  Redis (WebSocket 会话状态)              │
  └─────────────────────────────────────────┘
```

### 1.3 职责边界

| 职责 | 负责方 |
|------|--------|
| 音频采集与播放 | **上游应用** |
| speaker_id → 用户身份映射（如"爸爸""妈妈"） | **上游应用** |
| VAD 检测 | **网关** |
| 声纹 embedding 提取、存储、比对 | **网关** |
| MiniCPM-o 语音理解与回复生成 | **网关** |
| 语音场景 Tool Calling 编排 | **网关** |

---

## 2. 通用约定

### 2.1 认证

所有端点均需认证：

| 端点类型 | 认证方式 | 示例 |
|----------|---------|------|
| HTTP | `Authorization: Bearer <api_key>` 请求头 | `Authorization: Bearer sk-23h8ugn3828910h8g308979y4` |
| WebSocket | `api_key` Query 参数 | `ws://host:8081/v1/voice/stream?api_key=sk-23h8ugn3828910h8g308979y4` |

### 2.2 音频格式要求

**唯一接受格式：WAV (PCM16, 16kHz, mono)**

| 属性 | 要求 |
|------|------|
| 容器格式 | WAV (RIFF 头) |
| 编码 | PCM 16-bit signed little-endian |
| 采样率 | 16000 Hz |
| 声道 | 1 (mono) |
| MIME 类型 | `audio/wav` 或 `audio/wave` |
| Magic Number | 文件头以 `RIFF` (0x52494646) 开头 |

不符合上述格式的音频将返回 `400 E6001 AUDIO_FORMAT_INVALID`。

**WAV 文件生成参考（Python）**：

```python
import wave
import struct

def create_wav(pcm_samples: list[int], filename: str):
    """生成符合要求的 WAV 文件"""
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)       # mono
        wf.setsampwidth(2)       # 16-bit = 2 bytes
        wf.setframerate(16000)   # 16kHz
        wf.writeframes(struct.pack(f"<{len(pcm_samples)}h", *pcm_samples))
```

**格式转换（ffmpeg）**：

```bash
ffmpeg -i input.mp3 -ar 16000 -ac 1 -acodec pcm_s16le output.wav
```

### 2.3 音频传输方式

HTTP 端点支持两种音频输入方式：

**方式一：JSON body（base64 编码）**

```json
{
    "audio": "<base64 编码的 WAV 文件全部字节>"
}
```

适用场景：小文件（<1MB），简单集成。

**方式二：multipart/form-data（文件上传）**

```
Content-Type: multipart/form-data
audio: <文件字段>
```

适用场景：大文件，避免 base64 膨胀（+33% 体积）。

### 2.4 大小与时长限制

| 端点 | 最大文件大小 | 最大时长 | 最小时长 |
|------|------------|---------|---------|
| `POST /v1/voice/vad` | 10 MB | 60 秒 | — |
| `POST /v1/voice/speakers`（注册） | 10 MB | 30 秒 | 10 秒 |
| `POST /v1/voice/speakers/identify` | 10 MB | 60 秒 | — |
| `POST /v1/chat/completions`（音频） | 10 MB | 60 秒 | — |

超限返回 `400 E6002 AUDIO_TOO_LARGE`。

### 2.5 通用错误响应格式

```json
{
    "error": {
        "code": "E6001",
        "type": "validation_error",
        "message": "音频格式不支持，仅接受 WAV (PCM16, 16kHz, mono)",
        "details": {
            "supported_formats": ["audio/wav"],
            "received_format": "audio/mp3"
        }
    }
}
```

---

## 3. HTTP 端点：VAD 语音活动检测

### `POST /v1/voice/vad`

检测音频中是否包含人声，返回整体判断和各语音段的起止时间。

#### 请求（JSON body）

```json
{
    "audio": "UklGRiQAAABXQVZFZm10IBAAAA...",
    "threshold": 0.5
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `audio` | string (base64) | **是** | — | base64 编码的 WAV 音频 |
| `threshold` | float | 否 | 0.5 | 语音概率阈值 (0.0-1.0) |

#### 请求（multipart/form-data）

```
POST /v1/voice/vad
Content-Type: multipart/form-data

audio: <WAV 文件>
threshold: 0.5
```

#### 成功响应 `200 OK`

```json
{
    "is_speech": true,
    "speech_prob": 0.87,
    "segments": [
        {
            "start_ms": 200,
            "end_ms": 3500,
            "speech_prob": 0.92
        },
        {
            "start_ms": 4200,
            "end_ms": 6800,
            "speech_prob": 0.85
        }
    ],
    "duration_ms": 8000
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_speech` | bool | 整段音频是否包含语音（`speech_prob >= threshold`） |
| `speech_prob` | float | 整段音频的语音概率 (0.0-1.0) |
| `segments` | array | 各语音段详情（可能为空数组） |
| `segments[].start_ms` | int | 语音段起始时间（毫秒） |
| `segments[].end_ms` | int | 语音段结束时间（毫秒） |
| `segments[].speech_prob` | float | 该段语音概率 |
| `duration_ms` | int | 音频总时长（毫秒） |

#### 错误响应

| HTTP 状态码 | 错误码 | 场景 |
|:----------:|--------|------|
| 400 | E6001 | 音频格式不支持 |
| 400 | E6002 | 音频超过 10MB 或 60 秒 |
| 401 | E2001 | API Key 无效 |
| 503 | E6004 | VAD 服务不可用（模型加载失败） |

---

## 4. HTTP 端点：声纹管理

### 4.1 声纹注册 `POST /v1/voice/speakers`

上游发送一段音频，网关自行提取 embedding、生成 speaker_id、存储数据。

#### 请求（JSON body）

```json
{
    "audio": "UklGRiQAAABXQVZFZm10IBAAAA...",
    "speaker_id": null
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `audio` | string (base64) | **是** | — | base64 编码的 WAV 音频（**10-30 秒**） |
| `speaker_id` | string \| null | 否 | null | 指定则覆盖已有声纹，不传则自动生成 |

#### 请求（multipart/form-data）

```
POST /v1/voice/speakers
Content-Type: multipart/form-data

audio: <WAV 文件>
speaker_id: <可选>
```

#### 成功响应

**新建：`201 Created`**

```json
{
    "speaker_id": "spk_f7e2a1b3",
    "quality_score": 0.85,
    "created": true
}
```

**覆盖已有：`200 OK`**

```json
{
    "speaker_id": "spk_f7e2a1b3",
    "quality_score": 0.78,
    "created": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `speaker_id` | string | 声纹唯一标识（格式：`spk_` + 8 字符随机串） |
| `quality_score` | float | 注册音频质量评分 (0.0-1.0)，仅供参考，不影响注册 |
| `created` | bool | `true`=新建，`false`=覆盖已有 |

**关于 quality_score**：

| 评分范围 | 含义 | 建议 |
|---------|------|------|
| 0.8-1.0 | 优质（安静环境、清晰语音） | 无需操作 |
| 0.5-0.8 | 可用（轻微噪声） | 可提示用户在更安静环境重录 |
| 0.0-0.5 | 质量较低 | 建议提示用户重录，但网关**始终接受注册** |

#### 错误响应

| HTTP 状态码 | 错误码 | 场景 |
|:----------:|--------|------|
| 400 | E6001 | 音频格式不支持 |
| 400 | E6002 | 音频时长不在 10-30 秒范围 |
| 401 | E2001 | API Key 无效 |
| 503 | E6004 | 声纹服务不可用 |

---

### 4.2 声纹匹配 `POST /v1/voice/speakers/identify`

发送音频，网关与所有已存储声纹比对。

#### 请求（JSON body）

```json
{
    "audio": "UklGRiQAAABXQVZFZm10IBAAAA...",
    "threshold": 0.6
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `audio` | string (base64) | **是** | — | base64 编码的 WAV 音频 |
| `threshold` | float | 否 | 0.6 | 匹配置信度阈值 (0.0-1.0) |

#### 成功响应 `200 OK`

**匹配到已注册说话人**：

```json
{
    "identified": true,
    "speaker_id": "spk_f7e2a1b3",
    "confidence": 0.82
}
```

**未匹配到**：

```json
{
    "identified": false,
    "speaker_id": null,
    "confidence": 0.31
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `identified` | bool | 是否匹配到已注册说话人 |
| `speaker_id` | string \| null | 匹配到的 speaker_id，未匹配时为 null |
| `confidence` | float | 匹配置信度 (0.0-1.0)，`>= threshold` 视为匹配 |

**关于 threshold 调优**：

| 阈值 | 效果 | 适用场景 |
|:----:|------|---------|
| 0.5 | 宽松匹配（误匹配率高） | 注册人数少（<5人） |
| 0.6 | 默认值 | 家庭场景推荐 |
| 0.7 | 严格匹配（拒绝率高） | 安全敏感场景 |

---

### 4.3 声纹列表 `GET /v1/voice/speakers`

查询所有已注册的声纹记录。

#### 成功响应 `200 OK`

```json
{
    "speakers": [
        {
            "speaker_id": "spk_f7e2a1b3",
            "quality_score": 0.85,
            "created_at": "2026-02-14T10:30:00Z",
            "updated_at": "2026-02-14T10:30:00Z",
            "expires_at": "2027-02-14T10:30:00Z"
        },
        {
            "speaker_id": "spk_a3c9d2e1",
            "quality_score": 0.72,
            "created_at": "2026-02-15T08:20:00Z",
            "updated_at": "2026-02-15T08:20:00Z",
            "expires_at": "2027-02-15T08:20:00Z"
        }
    ],
    "total": 2
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `speakers` | array | 声纹列表 |
| `speakers[].speaker_id` | string | 声纹唯一标识 |
| `speakers[].quality_score` | float | 注册时的音频质量评分 |
| `speakers[].created_at` | string (ISO 8601) | 创建时间 |
| `speakers[].updated_at` | string (ISO 8601) | 最后更新时间（覆盖注册时更新） |
| `speakers[].expires_at` | string (ISO 8601) | 过期时间（默认创建后 1 年） |
| `total` | int | 声纹总数 |

---

### 4.4 声纹删除 `DELETE /v1/voice/speakers/{speaker_id}`

物理删除声纹数据（含 embedding 和元信息），不做软删除。

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `speaker_id` | string | 要删除的声纹 ID |

#### 成功响应 `204 No Content`

无响应体。

#### 错误响应

| HTTP 状态码 | 错误码 | 场景 |
|:----------:|--------|------|
| 404 | E6003 | speaker_id 不存在 |

---

## 5. HTTP 端点：语音聊天（MiniCPM-o 音频交互）

### `POST /v1/chat/completions`（扩展现有端点）

在现有聊天推理接口基础上，新增音频消息类型和音频输出参数。

#### 请求示例

```json
{
    "model": "minicpm-o",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请描述一下你听到了什么"
                },
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAA..."
                    }
                }
            ]
        }
    ],
    "audio_output": true,
    "stream": true
}
```

#### 新增/扩展字段

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `model` | string | **是** | — | 必须为支持音频的模型（如 `minicpm-o`） |
| `messages[].content` | array | **是** | — | 消息内容列表，可混合 text + audio_url |
| `audio_output` | bool | 否 | false | 是否请求语音回复 |

#### 音频消息格式

消息内容中的音频通过 `audio_url` 类型传递：

```json
{
    "type": "audio_url",
    "audio_url": {
        "url": "data:audio/wav;base64,<base64 编码的 WAV 数据>"
    }
}
```

**注意**：
- `url` 字段使用 data URL 格式，**不支持** HTTP URL 引用
- 音频必须为 WAV (PCM16, 16kHz, mono)
- 单请求最多 5 段音频（受 `limit_mm_per_prompt.audio` 限制）
- 可与 `text`、`image_url` 混合使用（多模态输入）

#### 非流式响应扩展

```json
{
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1708000000,
    "model": "minicpm-o",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "我听到了一段中文语音，内容是关于天气的询问。"
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 128,
        "completion_tokens": 45,
        "total_tokens": 173
    },
    "audio_output_supported": false
}
```

| 新增字段 | 类型 | 说明 |
|----------|------|------|
| `audio_output_supported` | bool \| null | `false`=当前不支持音频输出；`null`=未请求 |

> **重要**：当前 vLLM 版本尚不支持 MiniCPM-o 音频输出，`audio_output_supported` 将始终为 `false`。请求 `audio_output: true` **不会报错**，仅返回文本回复并标记不支持。

#### 流式响应扩展（SSE）

当 `stream: true` 时，响应为 SSE 事件流：

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"minicpm-o","choices":[{"index":0,"delta":{"content":"我听到了","audio":null},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"minicpm-o","choices":[{"index":0,"delta":{"content":"一段中文语音","audio":null},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"minicpm-o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"audio_output_supported":false}

data: [DONE]
```

| delta 字段 | 类型 | 说明 |
|-----------|------|------|
| `delta.content` | string \| null | 文本增量 |
| `delta.audio` | string \| null | base64 PCM16 音频块（当前始终为 null） |

#### 错误响应

| HTTP 状态码 | 错误码 | 场景 |
|:----------:|--------|------|
| 400 | E6001 | 音频格式不支持（非 WAV PCM16 16kHz mono） |
| 400 | E6002 | 音频超过 10MB 或 60 秒 |
| 400 | E3001 | 指定模型不支持音频输入 |
| 401 | E2001 | API Key 无效 |
| 503 | E3002 | 模型不可用（MiniCPM-o 未加载） |

---

## 6. WebSocket 端点：持续监控模式

### 6.1 概述

WebSocket 持续监控模式是 HTTP 端点的**实时编排层**——底层复用 VAD、声纹、推理等服务实例，将"客户端轮询调用多个 HTTP 端点"变成"服务端自动编排、事件驱动推送"。

适用场景：树莓派等设备始终在线，持续采集音频流，用户随时开口即可对话。

| 能力 | HTTP 端点 | WebSocket 事件 |
|------|-----------|---------------|
| VAD 检测 | `POST /v1/voice/vad` | `vad.speech_start` / `vad.speech_end` |
| 声纹匹配 | `POST /v1/voice/speakers/identify` | `speaker.identified` |
| 语音推理 | `POST /v1/chat/completions` | `response.start` / `response.delta` / `response.end` |
| 声纹注册 | `POST /v1/voice/speakers` | **仅 HTTP**（一次性操作） |
| 声纹管理 | `GET/DELETE /v1/voice/speakers` | **仅 HTTP** |

### 6.2 连接建立

```
ws://{gateway_host}:8081/v1/voice/stream?api_key=sk-23h8ugn3828910h8g308979y4
```

- 认证方式：`api_key` Query 参数
- 认证失败：HTTP 401，拒绝 WebSocket 升级
- 认证成功：完成 WebSocket 握手

### 6.3 帧类型约定

| 帧类型 | 用途 | 方向 | 说明 |
|--------|------|------|------|
| **Binary** | 原始 PCM16 音频数据 | Client → Server | **无 WAV 头**，裸 PCM 字节 |
| **Text (JSON)** | 控制消息 + 事件通知 | 双向 | 统一 JSON 结构 |
| **Ping/Pong** | 连接保活 | 双向 | WebSocket 协议原生帧 |

### 6.4 JSON 消息统一结构

所有 Text 帧遵循统一结构：

```json
{
    "type": "message_type",
    "event_id": "evt_xxxx",
    "data": { ... }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `type` | string | **是** | 消息类型标识 |
| `event_id` | string \| null | 条件 | 服务端下行消息**必填**，客户端上行消息可选 |
| `data` | object | **是** | 消息负载 |

### 6.5 完整生命周期

```
Client                                Server
  |                                      |
  |---- WS Connect + api_key ----------->|
  |<---- session.created ----------------|  <- 立即发送
  |---- session.configure -------------->|  <- 必须在发送音频前完成
  |<---- session.configured -------------|
  |                                      |
  |---- [Binary PCM 帧 x N] ----------->|  <- 持续发送
  |<---- vad.speech_start ---------------|  <- 检测到语音起始
  |---- [Binary PCM 帧 x N] ----------->|  <- 继续发送
  |<---- vad.speech_end -----------------|  <- 检测到语音结束
  |<---- speaker.identified -------------|  <- 可选 (speaker_identify=true)
  |<---- response.start -----------------|  <- 推理开始
  |<---- response.delta (text) ----------|  x N (流式文本)
  |<---- response.delta (text+audio) ----|  x N (流式文本+音频)
  |<---- response.end -------------------|  <- 推理完成
  |                                      |
  |---- [Binary PCM 帧 x N] ----------->|  <- 下一轮对话...
  |          ...                         |
  |                                      |
  |---- WS Close ----------------------->|
```

### 6.6 会话生命周期消息

#### 6.6.1 `session.created`（Server → Client）

连接建立后服务端**立即**发送：

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

#### 6.6.2 `session.configure`（Client → Server）

**必须在发送音频帧之前完成**。配置当前会话参数：

```json
{
    "type": "session.configure",
    "data": {
        "vad_enabled": true,
        "vad_threshold": 0.5,
        "speaker_identify": true,
        "speaker_threshold": 0.6,
        "auto_respond": true,
        "audio_output": false,
        "model": "minicpm-o",
        "tool_calling_model": "gpt-4o",
        "chunk_duration_ms": 30
    }
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `vad_enabled` | bool | true | 启用 VAD 检测 |
| `vad_threshold` | float | 0.5 | VAD 语音概率阈值 (0.0-1.0) |
| `speaker_identify` | bool | false | 语音段结束后是否自动声纹匹配 |
| `speaker_threshold` | float | 0.6 | 声纹匹配置信度阈值 (0.0-1.0) |
| `auto_respond` | bool | true | 语音段结束后是否自动送入模型推理 |
| `audio_output` | bool | false | 是否请求音频回复 |
| `model` | string | "minicpm-o" | 推理模型名称 |
| `tool_calling_model` | string \| null | null | 远程 Tool Calling 模型（如 "gpt-4o"），null 时不启用 |
| `chunk_duration_ms` | int | 30 | 客户端每帧音频时长（毫秒），范围 10-100 |

**注意**：发送 `session.configure` 会**清空当前对话历史**。

#### 6.6.3 `session.configured`（Server → Client）

配置成功的确认：

```json
{
    "type": "session.configured",
    "event_id": "evt_002",
    "data": {
        "status": "ok"
    }
}
```

#### 6.6.4 `session.update`（Client → Server）

运行时动态调参，**不清空对话历史**。仅传入需要修改的字段：

```json
{
    "type": "session.update",
    "data": {
        "vad_threshold": 0.7,
        "speaker_identify": false
    }
}
```

### 6.7 音频流与 VAD 事件

#### 发送音频帧（Client → Server, Binary 帧）

持续发送原始 PCM16 音频数据（**无 WAV 头**）：

```
[960 bytes]  <- 30ms x 16000Hz x 2bytes = 960 bytes/帧
```

帧大小计算公式：`chunk_duration_ms x sample_rate / 1000 x 2 bytes`

| chunk_duration_ms | 帧大小 |
|:-----------------:|:------:|
| 20 | 640 bytes |
| 30（默认） | 960 bytes |
| 50 | 1600 bytes |
| 100 | 3200 bytes |

> 服务端内部将客户端帧对齐到 Silero VAD 要求的 512 样本/帧，对客户端透明。

#### `vad.speech_start`（Server → Client）

检测到语音起始：

```json
{
    "type": "vad.speech_start",
    "event_id": "evt_010",
    "data": {
        "timestamp_ms": 3200,
        "speech_prob": 0.87
    }
}
```

#### `vad.speech_end`（Server → Client）

检测到语音结束：

```json
{
    "type": "vad.speech_end",
    "event_id": "evt_015",
    "data": {
        "timestamp_ms": 6800,
        "duration_ms": 3600,
        "speech_prob": 0.12
    }
}
```

| 字段 | 说明 |
|------|------|
| `timestamp_ms` | 语音段结束时刻（相对连接建立） |
| `duration_ms` | 语音段持续时长 |
| `speech_prob` | 结束帧的语音概率（通常较低） |

### 6.8 声纹识别事件

#### `speaker.identified`（Server → Client）

在 `vad.speech_end` 之后、推理之前。仅当 `speaker_identify: true` 时触发。

**匹配到**：

```json
{
    "type": "speaker.identified",
    "event_id": "evt_016",
    "data": {
        "identified": true,
        "speaker_id": "spk_f7e2a1b3",
        "confidence": 0.82
    }
}
```

**未匹配到**：

```json
{
    "type": "speaker.identified",
    "event_id": "evt_017",
    "data": {
        "identified": false,
        "speaker_id": null,
        "confidence": 0.31
    }
}
```

### 6.9 推理响应事件

当 `auto_respond: true` 时自动触发，或通过 `input.commit` 手动触发。

#### `response.start`（Server → Client）

```json
{
    "type": "response.start",
    "event_id": "evt_020",
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

#### `response.delta`（Server → Client, 多次）

流式推送文本和/或音频增量：

```json
{
    "type": "response.delta",
    "event_id": "evt_021",
    "data": {
        "response_id": "resp_x1y2z3",
        "delta": {
            "content": "你好，今天天气不错。",
            "audio": null
        }
    }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `delta.content` | string \| null | 文本增量 |
| `delta.audio` | string \| null | base64 PCM16 音频块（当前版本为 null） |

> 客户端收到 `delta.audio` 非 null 时，解码 base64 后直接推送到扬声器播放。

#### `response.end`（Server → Client）

```json
{
    "type": "response.end",
    "event_id": "evt_030",
    "data": {
        "response_id": "resp_x1y2z3",
        "usage": {
            "input_tokens": 128,
            "output_tokens": 45,
            "audio_duration_ms": 0
        }
    }
}
```

### 6.10 控制消息

#### `input.commit`（Client → Server）

当 `auto_respond: false` 时，手动触发推理：

```json
{
    "type": "input.commit",
    "data": {}
}
```

服务端收到后，将当前累积的语音段送入推理管线。

#### `response.cancel`（Client → Server）

中断正在进行的推理：

```json
{
    "type": "response.cancel",
    "data": {
        "response_id": "resp_x1y2z3"
    }
}
```

服务端取消推理任务，停止发送 `response.delta`，**不发送** `response.end`。

### 6.11 错误事件

#### `error`（Server → Client）

```json
{
    "type": "error",
    "event_id": "evt_err_001",
    "data": {
        "code": "E3002",
        "message": "模型 minicpm-o 当前不可用，正在热加载中",
        "recoverable": true
    }
}
```

| 字段 | 说明 |
|------|------|
| `code` | 错误码（与 HTTP API 错误码体系统一） |
| `message` | 人类可读的错误描述 |
| `recoverable` | **关键字段**，决定客户端行为 |

**recoverable 行为指南**：

| recoverable | 含义 | 客户端行为 |
|:-----------:|------|-----------|
| `true` | 瞬态错误（模型加载中、远程 API 超时等） | **继续发送音频**，等待恢复 |
| `false` | 致命错误（认证失效、协议错误等） | **关闭连接并重新建立** |

### 6.12 心跳保活

使用 WebSocket 协议原生 **Ping/Pong 帧**（不走 JSON 消息）：

| 行为 | 方 | 频率/超时 |
|------|-----|----------|
| 发送 Ping | Client | 每 30 秒 |
| 回复 Pong | Server | 自动（WebSocket 协议） |
| 超时断开 | Server | 60 秒未收到 Ping |

超时断开后服务端自动清理：VAD 状态、音频缓冲、进行中的推理任务、对话历史。

### 6.13 对话历史管理

WebSocket 会话维护**最近 5 条问答**的对话历史，自动带入 MiniCPM-o 推理上下文：

| 事件 | 对话历史行为 |
|------|-------------|
| 用户语音输入 + 模型回复完成 | 追加 1 条问答到历史 |
| 历史超过 5 条 | 移除最早 1 条 |
| 发送 `session.configure` | **清空全部历史** |
| 连接断开 | **清空全部历史** |
| 发送 `session.update` | **不清空** |

### 6.14 语音 Tool Calling（远程模型协作）

当 `session.configure` 中配置了 `tool_calling_model`（如 `"gpt-4o"`）时，启用语音场景 Tool Calling：

```
用户语音 -> VAD 结束
  -> MiniCPM-o 提取结构化意图 JSON
  -> 网关转发给远程模型 (gpt-4o) 执行 tool calling
  -> 工具执行结果回传
  -> MiniCPM-o 根据工具结果生成最终回复
  -> response.delta 事件流推送
```

**客户端视角**：整个流程透明，客户端仅接收标准 `response.*` 事件，无需感知 tool calling 细节。

**降级行为**：
- MiniCPM-o 输出非 JSON 格式（未识别为工具意图）→ 视为普通对话回复，直接返回
- 远程模型不可用 → `error` 事件（`recoverable: true`），降级到纯对话模式

### 6.15 消息类型速查表

#### 客户端 → 服务端

| 类型 | 帧类型 | 说明 |
|------|--------|------|
| 音频帧 | Binary | 原始 PCM16 数据 |
| `session.configure` | Text JSON | 配置会话参数（清空对话历史） |
| `session.update` | Text JSON | 动态调参（不清空历史） |
| `input.commit` | Text JSON | 手动触发推理 |
| `response.cancel` | Text JSON | 中断推理 |
| Ping | Ping 帧 | 每 30 秒 |

#### 服务端 → 客户端

| 类型 | 帧类型 | 说明 |
|------|--------|------|
| `session.created` | Text JSON | 连接建立确认 |
| `session.configured` | Text JSON | 配置成功确认 |
| `vad.speech_start` | Text JSON | 检测到语音起始 |
| `vad.speech_end` | Text JSON | 检测到语音结束 |
| `speaker.identified` | Text JSON | 声纹识别结果 |
| `response.start` | Text JSON | 推理开始 |
| `response.delta` | Text JSON | 流式文本/音频增量 |
| `response.end` | Text JSON | 推理完成 |
| `error` | Text JSON | 错误通知 |
| Pong | Pong 帧 | 自动回复 |

---

## 7. 错误码参考

### 7.1 语音专用错误码（6xxx）

| 错误码 | HTTP | error_type | 说明 | 常见原因 |
|--------|:----:|-----------|------|---------|
| `E6001` | 400 | validation_error | 音频格式不支持 | 非 WAV、非 PCM16、非 16kHz、非 mono |
| `E6002` | 400 | validation_error | 音频超限 | 文件 > 10MB、时长 > 60s、注册时长不在 10-30s |
| `E6003` | 404 | not_found_error | 声纹不存在 | 删除不存在的 speaker_id |
| `E6004` | 503 | service_error | 语音服务不可用 | VAD/SpeechBrain 模型加载失败 |

### 7.2 复用的通用错误码

| 错误码 | HTTP | 说明 |
|--------|:----:|------|
| `E2001` | 401 | API Key 无效或缺失 |
| `E3001` | 404 | 模型不存在 |
| `E3002` | 503 | 模型不可用（未加载/加载中） |
| `E3003` | 504 | 模型超时 |

---

## 8. 对接示例

### 8.1 Python — VAD 检测

```python
import base64
import httpx

API_KEY = "sk-23h8ugn3828910h8g308979y4"
GATEWAY = "http://127.0.0.1:8081"

# 读取 WAV 文件并 base64 编码
with open("speech.wav", "rb") as f:
    audio_b64 = base64.b64encode(f.read()).decode()

resp = httpx.post(
    f"{GATEWAY}/v1/voice/vad",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={"audio": audio_b64, "threshold": 0.5},
)

result = resp.json()
print(f"包含语音: {result['is_speech']}")
print(f"语音概率: {result['speech_prob']:.2f}")
for seg in result["segments"]:
    print(f"  语音段: {seg['start_ms']}ms - {seg['end_ms']}ms "
          f"(prob={seg['speech_prob']:.2f})")
```

### 8.2 Python — 声纹注册 + 匹配

```python
import base64
import httpx

API_KEY = "sk-23h8ugn3828910h8g308979y4"
GATEWAY = "http://127.0.0.1:8081"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# 1. 声纹注册（需要 10-30 秒的音频）
with open("enroll_voice.wav", "rb") as f:
    enroll_b64 = base64.b64encode(f.read()).decode()

reg_resp = httpx.post(
    f"{GATEWAY}/v1/voice/speakers",
    headers=HEADERS,
    json={"audio": enroll_b64},
)
reg = reg_resp.json()
speaker_id = reg["speaker_id"]
print(f"注册成功: speaker_id={speaker_id}, 质量={reg['quality_score']:.2f}")

# 2. 声纹匹配
with open("test_voice.wav", "rb") as f:
    test_b64 = base64.b64encode(f.read()).decode()

id_resp = httpx.post(
    f"{GATEWAY}/v1/voice/speakers/identify",
    headers=HEADERS,
    json={"audio": test_b64, "threshold": 0.6},
)
match = id_resp.json()
if match["identified"]:
    print(f"匹配到: {match['speaker_id']} (置信度={match['confidence']:.2f})")
else:
    print(f"未匹配 (最高置信度={match['confidence']:.2f})")

# 3. 声纹列表
list_resp = httpx.get(f"{GATEWAY}/v1/voice/speakers", headers=HEADERS)
speakers = list_resp.json()
print(f"已注册 {speakers['total']} 个声纹")

# 4. 声纹删除
httpx.delete(f"{GATEWAY}/v1/voice/speakers/{speaker_id}", headers=HEADERS)
print(f"已删除: {speaker_id}")
```

### 8.3 Python — 语音聊天（非流式）

```python
import base64
import httpx

API_KEY = "sk-23h8ugn3828910h8g308979y4"
GATEWAY = "http://127.0.0.1:8081"

with open("question.wav", "rb") as f:
    audio_b64 = base64.b64encode(f.read()).decode()

resp = httpx.post(
    f"{GATEWAY}/v1/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "model": "minicpm-o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": f"data:audio/wav;base64,{audio_b64}"
                        },
                    }
                ],
            }
        ],
        "audio_output": False,
    },
    timeout=60.0,
)
result = resp.json()
print(f"回复: {result['choices'][0]['message']['content']}")
print(f"音频输出支持: {result.get('audio_output_supported')}")
```

### 8.4 Python — 语音聊天（流式）

```python
import base64
import json
import httpx

API_KEY = "sk-23h8ugn3828910h8g308979y4"
GATEWAY = "http://127.0.0.1:8081"

with open("question.wav", "rb") as f:
    audio_b64 = base64.b64encode(f.read()).decode()

with httpx.stream(
    "POST",
    f"{GATEWAY}/v1/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "model": "minicpm-o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请用中文回答"},
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": f"data:audio/wav;base64,{audio_b64}"
                        },
                    },
                ],
            }
        ],
        "stream": True,
    },
    timeout=60.0,
) as resp:
    for line in resp.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk = json.loads(line[6:])
            delta = chunk["choices"][0].get("delta", {})
            if delta.get("content"):
                print(delta["content"], end="", flush=True)
    print()
```

### 8.5 Python — WebSocket 持续监控

```python
import asyncio
import json
import wave
import websockets

API_KEY = "sk-23h8ugn3828910h8g308979y4"
WS_URL = f"ws://127.0.0.1:8081/v1/voice/stream?api_key={API_KEY}"

async def voice_monitor():
    async with websockets.connect(WS_URL, ping_interval=30) as ws:
        # 1. 等待 session.created
        msg = json.loads(await ws.recv())
        assert msg["type"] == "session.created"
        session_id = msg["data"]["session_id"]
        print(f"会话建立: {session_id}")

        # 2. 配置会话
        await ws.send(json.dumps({
            "type": "session.configure",
            "data": {
                "vad_enabled": True,
                "vad_threshold": 0.5,
                "speaker_identify": True,
                "speaker_threshold": 0.6,
                "auto_respond": True,
                "audio_output": False,
                "model": "minicpm-o",
                "chunk_duration_ms": 30,
            },
        }))
        configured = json.loads(await ws.recv())
        assert configured["type"] == "session.configured"
        print("会话配置完成")

        # 3. 发送音频帧（从 WAV 文件读取 PCM 数据）
        async def send_audio():
            with wave.open("continuous_audio.wav", "rb") as wf:
                chunk_frames = 480  # 480 frames = 960 bytes (16-bit)
                while True:
                    data = wf.readframes(chunk_frames)
                    if not data:
                        break
                    await ws.send(data)  # Binary 帧
                    await asyncio.sleep(0.03)  # 模拟实时 30ms/帧

        # 4. 接收事件
        async def receive_events():
            while True:
                msg = await ws.recv()
                if isinstance(msg, str):
                    event = json.loads(msg)
                    t = event["type"]
                    d = event["data"]

                    if t == "vad.speech_start":
                        print(f"\n[VAD] 语音开始 @ {d['timestamp_ms']}ms")
                    elif t == "vad.speech_end":
                        print(f"[VAD] 语音结束 ({d['duration_ms']}ms)")
                    elif t == "speaker.identified":
                        if d["identified"]:
                            print(f"[声纹] {d['speaker_id']} "
                                  f"(置信度={d['confidence']:.2f})")
                        else:
                            print(f"[声纹] 未识别")
                    elif t == "response.start":
                        print(f"[推理] model={d['model']}")
                    elif t == "response.delta":
                        text = d["delta"].get("content", "")
                        if text:
                            print(text, end="", flush=True)
                    elif t == "response.end":
                        usage = d["usage"]
                        print(f"\n[完成] tokens: {usage['input_tokens']}"
                              f"+{usage['output_tokens']}")
                    elif t == "error":
                        print(f"[错误] {d['code']}: {d['message']}")
                        if not d["recoverable"]:
                            return

        await asyncio.gather(send_audio(), receive_events())

asyncio.run(voice_monitor())
```

### 8.6 Python — 树莓派实时麦克风 + WebSocket

```python
"""
树莓派实时语音交互示例
依赖: pip install websockets pyaudio
"""
import asyncio
import json
import pyaudio
import websockets

API_KEY = "sk-23h8ugn3828910h8g308979y4"
# 通过 frpc visitor 访问网关
WS_URL = f"ws://127.0.0.1:8100/v1/voice/stream?api_key={API_KEY}"

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 30
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 480 frames
CHUNK_BYTES = CHUNK_FRAMES * 2  # 960 bytes (16-bit)

async def main():
    pa = pyaudio.PyAudio()
    mic = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_FRAMES,
    )

    async with websockets.connect(WS_URL, ping_interval=30) as ws:
        created = json.loads(await ws.recv())
        print(f"会话: {created['data']['session_id']}")

        await ws.send(json.dumps({
            "type": "session.configure",
            "data": {
                "vad_enabled": True,
                "speaker_identify": True,
                "auto_respond": True,
                "model": "minicpm-o",
                "chunk_duration_ms": CHUNK_MS,
            },
        }))
        await ws.recv()  # session.configured
        print("开始监听...")

        async def capture():
            while True:
                pcm = mic.read(CHUNK_FRAMES, exception_on_overflow=False)
                await ws.send(pcm)
                await asyncio.sleep(CHUNK_MS / 1000)

        async def listen():
            while True:
                msg = await ws.recv()
                if isinstance(msg, str):
                    evt = json.loads(msg)
                    t, d = evt["type"], evt["data"]

                    if t == "vad.speech_start":
                        print(">>> 检测到说话")
                    elif t == "vad.speech_end":
                        print(f">>> 说话结束 ({d['duration_ms']}ms)")
                    elif t == "speaker.identified" and d["identified"]:
                        print(f">>> 说话人: {d['speaker_id']}")
                    elif t == "response.delta" and d["delta"].get("content"):
                        print(d["delta"]["content"], end="", flush=True)
                    elif t == "response.end":
                        print()

        try:
            await asyncio.gather(capture(), listen())
        finally:
            mic.stop_stream()
            mic.close()
            pa.terminate()

asyncio.run(main())
```

### 8.7 curl 示例

```bash
# VAD 检测 (multipart)
curl -X POST http://127.0.0.1:8081/v1/voice/vad \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "audio=@speech.wav" \
  -F "threshold=0.5"

# 声纹注册
curl -X POST http://127.0.0.1:8081/v1/voice/speakers \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "audio=@enroll_15sec.wav"

# 声纹匹配
curl -X POST http://127.0.0.1:8081/v1/voice/speakers/identify \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "audio=@test_voice.wav" \
  -F "threshold=0.6"

# 声纹列表
curl -X GET http://127.0.0.1:8081/v1/voice/speakers \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"

# 声纹删除
curl -X DELETE http://127.0.0.1:8081/v1/voice/speakers/spk_f7e2a1b3 \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"
```

### 8.8 JavaScript — WebSocket 持续监控

```javascript
const WebSocket = require("ws");

const API_KEY = "sk-23h8ugn3828910h8g308979y4";
const ws = new WebSocket(
  `ws://127.0.0.1:8081/v1/voice/stream?api_key=${API_KEY}`
);

ws.on("open", () => console.log("连接建立"));

ws.on("message", (data, isBinary) => {
  if (!isBinary) {
    const event = JSON.parse(data.toString());
    switch (event.type) {
      case "session.created":
        console.log(`会话: ${event.data.session_id}`);
        ws.send(JSON.stringify({
          type: "session.configure",
          data: {
            vad_enabled: true,
            speaker_identify: true,
            auto_respond: true,
            model: "minicpm-o",
          },
        }));
        break;
      case "session.configured":
        console.log("配置完成");
        // 开始发送音频...
        break;
      case "vad.speech_start":
        console.log(`语音开始 @ ${event.data.timestamp_ms}ms`);
        break;
      case "vad.speech_end":
        console.log(`语音结束 (${event.data.duration_ms}ms)`);
        break;
      case "speaker.identified":
        if (event.data.identified)
          console.log(`说话人: ${event.data.speaker_id}`);
        break;
      case "response.delta":
        if (event.data.delta.content)
          process.stdout.write(event.data.delta.content);
        break;
      case "response.end":
        console.log("\n推理完成");
        break;
      case "error":
        console.error(`${event.data.code}: ${event.data.message}`);
        if (!event.data.recoverable) ws.close();
        break;
    }
  }
});

// 每 30 秒 Ping 保活
setInterval(() => ws.ping(), 30000);
```

---

## 9. FAQ

### Q1: 上游应用需要安装什么依赖？

**无特殊依赖**。上游只需要：
- HTTP 客户端（任意语言的 HTTP 库）
- WebSocket 客户端（如 Python `websockets`、JS `ws`）
- 音频采集能力（如 `pyaudio`、浏览器 `MediaRecorder`）

所有 AI 处理（VAD、声纹、推理）均在网关侧完成。

### Q2: speaker_id 和用户身份怎么对应？

网关只负责生成和管理 `speaker_id`（如 `spk_f7e2a1b3`）。将 speaker_id 映射到用户身份（如"爸爸""妈妈"）是上游应用的职责。建议在上游数据库维护映射表：

| speaker_id | name | role |
|------------|------|------|
| spk_f7e2a1b3 | 爸爸 | parent |
| spk_a3c9d2e1 | 妈妈 | parent |

### Q3: WebSocket 断开后对话历史还在吗？

**不在**。连接断开后服务端清理所有会话资源（含对话历史）。重连后从零开始。

### Q4: 可以同时开多个 WebSocket 连接吗？

当前为**家庭场景单连接**设计。同一时刻建议仅保持 1 个 WebSocket 连接。

### Q5: MiniCPM-o 当前支持音频输出吗？

**当前不支持**。`audio_output` 参数可以传，但 `audio_output_supported` 会返回 `false`，仅返回文本回复。后续版本补充。

### Q6: WebSocket 模式下的 Tool Calling 对客户端透明吗？

**完全透明**。客户端只看到标准 `response.*` 事件。网关内部自动编排 MiniCPM-o 意图提取 → 远程模型 tool calling → MiniCPM-o 最终回复。

### Q7: 音频格式不对怎么诊断？

收到 `E6001` 时检查：

```bash
# 检查音频属性
ffprobe -v error -show_entries stream=codec_name,sample_rate,channels audio.wav

# 转换为正确格式
ffmpeg -i input.mp3 -ar 16000 -ac 1 -acodec pcm_s16le output.wav
```

必须满足：WAV 容器、PCM 16-bit、16000 Hz、mono。

### Q8: 声纹数据会过期吗？

会。默认保留 **1 年**（`expires_at = created_at + 365 天`）。过期后网关自动清理。重新注册（覆盖）会重置过期时间。

### Q9: 通过 frpc 访问时端口怎么配？

上游应用通过 frpc STCP visitor 访问网关。visitor 配置的 `bindPort`（如 8100）即为本地访问端口：

```
HTTP:      http://127.0.0.1:8100/v1/voice/vad
WebSocket: ws://127.0.0.1:8100/v1/voice/stream?api_key=sk-xxx
```

---

> **文档版本**: 2.0.0 | 基于 spec.md v2026-02-24, plan.md v2026-02-14, tasks.md v2026-02-24
