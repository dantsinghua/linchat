# LLM Gateway × LinChat 对接指南

> **版本**: 3.0.0
> **更新日期**: 2026-03-02
> **适用版本**: LLM Gateway v3.x（含语音 IO 原子化服务）

本文档面向 LinChat 应用开发者，完整说明 LLM Gateway 提供的全部模型服务接口、请求/响应契约和对接方式。

---

## 目录

1. [网络接入](#1-网络接入)
2. [认证](#2-认证)
3. [可用模型一览](#3-可用模型一览)
4. [聊天推理 API](#4-聊天推理-api)
5. [文本向量化 API](#5-文本向量化-api)
6. [ASR 语音转文字](#6-asr-语音转文字)（REST + WebSocket 流式）
7. [TTS 文字转语音](#7-tts-文字转语音)
8. [VAD 语音活动检测](#8-vad-语音活动检测)
9. [声纹识别](#9-声纹识别)
10. [文档解析](#10-文档解析)
11. [安全护栏](#11-安全护栏)
12. [模型管理](#12-模型管理)
13. [健康检查与监控](#13-健康检查与监控)
14. [错误码参考](#14-错误码参考)
15. [音频格式要求](#15-音频格式要求)
16. [对接最佳实践](#16-对接最佳实践)

---

## 1. 网络接入

### 1.1 接入架构

LinChat 通过 frpc STCP Visitor 访问 LLM Gateway，**零公网端口暴露**：

```
LinChat 应用
  └── http://127.0.0.1:8100  ← frpc visitor bindPort
        └── frpc visitor (STCP)
              └── frps 中转（wstunnel TLS 隧道）
                    └── frpc proxy (Windows 侧)
                          └── LLM Gateway (WSL2:8081)
```

### 1.2 LinChat 侧 frpc 配置

**方式 A：通过 wstunnel 接入（推荐）**

```toml
# frpc.toml — LinChat 侧
serverAddr = "127.0.0.1"          # wstunnel 本地端口
serverPort = 7443
auth.method = "token"
auth.token = "frps@Greydan2026!Xin#Secure"
transport.protocol = "tcp"
transport.tls.enable = false       # TLS 由 wstunnel 处理
transport.tcpMux = true            # 🔴 必须为 true，与 proxy 侧一致

[[visitors]]
name = "llm-gateway-visitor"
type = "stcp"
serverName = "llm-gateway"
secretKey = "LLMGateway@2026!Secure"
bindAddr = "127.0.0.1"
bindPort = 8100                    # LinChat 通过此端口访问网关
```

**方式 B：直连 frps（无 wstunnel，需 TLS）**

```toml
serverAddr = "www.greydan.xin"
serverPort = 7443
auth.method = "token"
auth.token = "frps@Greydan2026!Xin#Secure"
transport.protocol = "tcp"
transport.tls.enable = true
transport.tls.serverName = "www.greydan.xin"
transport.tcpMux = true            # 🔴 必须为 true

[[visitors]]
name = "llm-gateway-visitor"
type = "stcp"
serverName = "llm-gateway"
secretKey = "LLMGateway@2026!Secure"
bindAddr = "127.0.0.1"
bindPort = 8100
```

### 1.3 基础 URL

```
BASE_URL = http://127.0.0.1:8100
```

以下所有示例中的 `$BASE` 均代表此地址。

---

## 2. 认证

所有 API 请求（健康检查除外）需携带 API Key：

```
Authorization: Bearer sk-23h8ugn3828910h8g308979y4
```

**免认证端点**：`/health`、`/health/live`、`/health/ready`、`/health/gpu`、`/metrics`

---

## 3. 可用模型一览

### 3.1 本地推理模型

| 模型 ID | 类型 | 引擎 | 显存 | 能力 |
|---------|------|------|------|------|
| `qwen3-8b` | 文本 LLM | vLLM | 16GB | 对话、Tool Calling、思维链 |
| `qwen3-30b` | 文本 LLM | llama.cpp | 18GB | 对话（高质量推理） |
| `qwen2.5-coder` | 代码 LLM | vLLM | 15GB | 代码生成、代码补全 |
| `qwen2.5-vl` | 视觉语言 | vLLM | 15GB | 图片理解、文档 OCR |
| `minicpm-v` | 视觉语言 | vLLM | 18GB | 图片理解、多图推理、文档 OCR |
| `minicpm-o` | 多模态 | vLLM | 18GB | 图片/音频/视频理解、文档 OCR |
| `glm-ocr` | 视觉语言 | vLLM | 6GB | 文档 OCR（轻量） |
| `qwen3-embedding` | Embedding | vLLM | 4GB | 文本向量化 |

### 3.2 语音模型（常驻，无需加载）

| 模型 ID | 类型 | 推理设备 | 说明 |
|---------|------|----------|------|
| `sensevoice` | ASR | CPU | 语音转文字（SenseVoice-Small） |
| `kokoro-tts` | TTS | GPU 0.55GB | 文字转语音（Kokoro-82M，9 种中文声音） |

### 3.3 远程转发模型

| 模型 ID | 服务商 | 上下文长度 | 说明 |
|---------|--------|-----------|------|
| `gpt-4o` | OpenAI | 128K | GPT-4o |
| `deepseek-chat` | DeepSeek | 65.5K | DeepSeek-V3 |
| `deepseek-v3-1-terminus` | 火山引擎 | 65.5K | DeepSeek-V3.1 |

> **注意**：本地模型支持动态热切换，同时只运行 1-2 个本地模型（受 GPU 显存限制）。远程模型无此限制。

---

## 4. 聊天推理 API

### `POST /v1/chat/completions`

OpenAI 兼容接口，支持流式/非流式、Tool Calling、多模态输入、安全护栏。

#### 请求

```json
{
  "model": "qwen3-8b",
  "messages": [
    {"role": "system", "content": "你是一个助手"},
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.7,
  "max_tokens": 2048,
  "top_p": null,
  "stream": false,
  "enable_thinking": null,
  "tools": null,
  "tool_choice": null,
  "guardrails_enabled": true,
  "guardrails_level": "fast",
  "include_usage": false
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `model` | string | 是 | - | 模型 ID |
| `messages` | array | 是 | - | 消息列表（1-100 条） |
| `temperature` | float | 否 | 0.7 | 采样温度（0.0-2.0） |
| `max_tokens` | int | 否 | null | 最大生成 token 数（1-128000） |
| `stream` | bool | 否 | false | 是否流式返回 |
| `enable_thinking` | bool | 否 | null | 启用思维链（仅 Qwen3 系列） |
| `tools` | array | 否 | null | 可用工具定义 |
| `tool_choice` | string/object | 否 | null | 工具选择策略 |
| `guardrails_enabled` | bool | 否 | true | 启用安全护栏 |
| `guardrails_level` | string | 否 | "fast" | 护栏级别（fast/standard/deep） |
| `include_usage` | bool | 否 | false | 返回护栏元数据 |

#### 非流式响应

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1709366400,
  "model": "qwen3-8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！有什么可以帮你的？"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 15,
    "total_tokens": 35
  }
}
```

#### 流式响应（SSE）

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1709366400,"model":"qwen3-8b","choices":[{"index":0,"delta":{"role":"assistant","content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1709366400,"model":"qwen3-8b","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1709366400,"model":"qwen3-8b","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":2,"total_tokens":22}}

data: [DONE]
```

#### 多模态输入

用 `content` 数组传入多种类型内容：

```json
{
  "model": "minicpm-o",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "描述这张图片"},
      {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
    ]
  }]
}
```

**支持的 content 类型：**

| type | 格式 | 适用模型 |
|------|------|---------|
| `text` | `{"type": "text", "text": "..."}` | 所有模型 |
| `image_url` | `{"type": "image_url", "image_url": {"url": "..."}}` | qwen2.5-vl, minicpm-v, minicpm-o, glm-ocr |
| `audio_url` | `{"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,..."}}` | minicpm-o |
| `video_url` | `{"type": "video_url", "video_url": {"url": "..."}}` | minicpm-o |

> **🔴 音频必须用 `audio_url` + Data URI**（`data:audio/wav;base64,...`），不支持 OpenAI 的 `input_audio` 格式。

#### Tool Calling

```json
{
  "model": "qwen3-8b",
  "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "城市名"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

`tool_choice` 取值：`"auto"` | `"none"` | `"required"` | `{"type": "function", "function": {"name": "..."}}`

#### curl 示例

```bash
# 基础对话
curl -X POST $BASE/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 流式对话
curl -X POST $BASE/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "写一首诗"}],
    "stream": true
  }'

# 远程模型
curl -X POST $BASE/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# 启用深度护栏
curl -X POST $BASE/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "用户输入"}],
    "guardrails_level": "deep",
    "include_usage": true
  }'
```

---

## 5. 文本向量化 API

### `POST /v1/embeddings`

```json
// 请求
{
  "model": "qwen3-embedding",
  "input": "要向量化的文本"
}
// 或批量
{
  "model": "qwen3-embedding",
  "input": ["文本1", "文本2", "文本3"]
}
```

```json
// 响应
{
  "object": "list",
  "data": [
    {"object": "embedding", "embedding": [0.012, -0.034, ...], "index": 0}
  ],
  "model": "qwen3-embedding",
  "usage": {"prompt_tokens": 10, "total_tokens": 10}
}
```

---

## 6. ASR 语音转文字

### `POST /v1/audio/transcriptions`

将语音音频转为文字。sherpa-onnx SenseVoice 引擎，CPU 推理，支持中/英/日/韩自动检测。

#### 请求（multipart/form-data）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | binary | 是 | WAV 音频文件（PCM16, 16kHz, mono） |
| `model` | string | 否 | 模型名，默认 `sensevoice` |
| `language` | string | 否 | 语言代码（`auto`/`zh`/`en`/`ja`/`ko`），默认自动检测 |

#### 响应

```json
{
  "text": "你好，世界",
  "language": "zh",
  "duration_ms": 3200
}
```

#### curl 示例

```bash
curl -X POST $BASE/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@recording.wav" \
  -F "model=sensevoice"
```

#### 音频要求

- 格式：WAV PCM16, 16kHz, 单声道
- 大小：≤ 10MB
- 时长：≤ 300 秒（5 分钟）

### `WS /v1/audio/transcriptions/stream`（WebSocket 流式）

实时流式语音转录。内置 VAD 人声过滤（只缓存人声帧，丢弃噪音/静音），支持手动 commit 和自动 commit 两种触发模式。适用于 LinChat 边录边转录场景，**无需再单独调用 VAD + ASR 两个接口**。

#### 连接

```
ws://$BASE/v1/audio/transcriptions/stream?api_key=sk-23h8ugn3828910h8g308979y4
```

| Close Code | 说明 |
|------------|------|
| `4001` | 认证失败 |
| `4002` | ASR 服务不可用 |
| `4003` | VAD 服务不可用（人声过滤是必需功能） |

连接成功后立即收到 `session.created` 事件。

#### 协议

**客户端 → 服务端：**

| 帧类型 | 内容 | 说明 |
|--------|------|------|
| Binary | 原始 PCM16 字节 | 16kHz mono，任意长度，单帧最大 64KB |
| Text | `{"type": "commit"}` | 手动触发转录（Push-to-Talk） |
| Text | `{"type": "clear"}` | 丢弃当前缓冲区 |
| Text | `{"type": "configure", ...}` | 运行时配置（见下表） |

**configure 可配置参数：**

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `auto_commit` | bool | `false` | — | `true` = speech_end 后自动触发转录 |
| `vad_threshold` | float | `0.5` | 0.0-1.0 | VAD 语音概率阈值 |
| `speech_pad_ms` | int | `2000` | 300-3000 | 语音段合并等待时长（仅 auto_commit 模式生效） |
| `language` | string | `"auto"` | — | 语言提示（`auto`/`zh`/`en`/`ja`/`ko`） |

**服务端 → 客户端：**

| 事件 | 格式 | 说明 |
|------|------|------|
| `session.created` | `{"type": "session.created", "session_id": "...", "config": {...}}` | 连接建立后立即发送 |
| `session.updated` | `{"type": "session.updated", "config": {...}}` | configure 确认 |
| `vad.speech_start` | `{"type": "vad.speech_start", "timestamp_ms": 100, "speech_prob": 0.87}` | 检测到人声开始 |
| `vad.speech_end` | `{"type": "vad.speech_end", "timestamp_ms": 3200, "duration_ms": 3100}` | 人声结束 |
| `transcription.started` | `{"type": "transcription.started"}` | ASR 推理开始 |
| `transcription.completed` | `{"type": "transcription.completed", "text": "...", "language": "zh", "duration_ms": 3200}` | 转录结果 |
| `transcription.failed` | `{"type": "transcription.failed", "error": "..."}` | 转录失败 |
| `buffer.cleared` | `{"type": "buffer.cleared"}` | clear 确认 |
| `error` | `{"type": "error", "message": "..."}` | 帧超大等错误 |

#### 两种触发模式

**模式 A：手动 commit（默认，`auto_commit: false`）**

客户端自行决定何时触发转录，发送 `{"type": "commit"}` 即可。适用于 Push-to-Talk 场景。

**模式 B：自动 commit（`auto_commit: true`）**

VAD 检测到 speech_end 后，等待 `speech_pad_ms` 毫秒（默认 2 秒）。如果期间没有新的 speech_start，则自动触发转录。如果有新的 speech_start，则取消定时器，将多段语音合并为一次转录。适用于自由对话场景。

```
speech_end → 启动定时器（2s）
  ├─ 定时器到期 → 自动转录
  └─ 新 speech_start → 取消定时器 → 合并为同一段
```

#### 双缓冲机制

推理期间收到的新人声帧写入 pending buffer，推理完成后自动切换为 active buffer，确保不丢失音频。推理中收到 commit 会返回 error 事件（`"transcription already in progress"`）。

#### Python 示例

```python
import asyncio, json, websockets

async def stream_asr():
    uri = "ws://127.0.0.1:8100/v1/audio/transcriptions/stream?api_key=sk-23h8ugn3828910h8g308979y4"
    async with websockets.connect(uri) as ws:
        # 等待 session.created
        session = json.loads(await ws.recv())
        print(f"Session: {session['session_id']}")

        # 切换到自动 commit 模式
        await ws.send(json.dumps({
            "type": "configure",
            "auto_commit": True,
            "speech_pad_ms": 1500
        }))
        updated = json.loads(await ws.recv())
        print(f"Config: {updated['config']}")

        # 持续发送麦克风 PCM 帧
        while recording:
            pcm_chunk = get_audio_chunk()  # PCM16 16kHz mono
            await ws.send(pcm_chunk)

            # 非阻塞检查服务端事件
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.001)
                    event = json.loads(msg)
                    if event["type"] == "vad.speech_start":
                        print(f"语音开始: {event['timestamp_ms']}ms")
                    elif event["type"] == "vad.speech_end":
                        print(f"语音结束: {event['timestamp_ms']}ms")
                    elif event["type"] == "transcription.completed":
                        text = event["text"]
                        print(f"转录: {text}")
                        send_to_llm(text)  # 发送给 LLM 问答
            except asyncio.TimeoutError:
                pass

asyncio.run(stream_asr())
```

#### 典型交互时序（自动 commit 模式）

```
客户端                              服务端
  |── ws connect ──────────────────→|
  |                                 |── session.created ──→
  |── configure auto_commit=true ──→|
  |                                 |── session.updated ──→
  |── PCM 帧（静音）──────────────→|             （丢弃，不缓存）
  |── PCM 帧（用户开始说话）──────→|
  |                                 |── vad.speech_start ──→
  |── PCM 帧（持续说话）──────────→|             （缓存人声帧）
  |── PCM 帧（用户停止说话）──────→|
  |                                 |── vad.speech_end ──→
  |── PCM 帧（静音）──────────────→|             （等待 speech_pad_ms）
  |                                 |── transcription.started ──→
  |                                 |── transcription.completed ──→
  |                                       text: "开饭时间早上9点至下午5点"
```

---

## 7. TTS 文字转语音

### `POST /v1/audio/speech`

将文字合成为语音。Kokoro-82M 引擎，GPU 推理，0.55GB 显存常驻。

#### 请求（JSON）

```json
{
  "model": "kokoro-tts",
  "input": "你好，欢迎使用语音服务",
  "voice": "zf_xiaobei",
  "response_format": "wav",
  "speed": 1.0
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `model` | string | 是 | - | 固定 `kokoro-tts` |
| `input` | string | 是 | - | 待合成文本（1-4096 字符） |
| `voice` | string | 否 | `zf_xiaobei` | 声音名称（见下表） |
| `response_format` | string | 否 | `wav` | 输出格式：`wav` / `pcm` |
| `speed` | float | 否 | 1.0 | 语速倍率（0.5-2.0） |
| `reference_audio` | string | 否 | null | 参考音频 base64（3-10 秒，音色克隆） |

#### 可用声音

| voice ID | 性别 | 名称 |
|----------|------|------|
| `zf_xiaobei` | 女 | 小北（默认） |
| `zf_xiaoyi` | 女 | 小漪 |
| `zf_xiaoni` | 女 | 小妮 |
| `zf_xiaowan` | 女 | 小婉 |
| `zf_xiaoyun` | 女 | 小云 |
| `zm_yunjian` | 男 | 云健 |
| `zm_yunxi` | 男 | 云熙 |
| `zm_yunxia` | 男 | 云霞 |
| `zm_yunyang` | 男 | 云阳 |

#### 响应

- `Content-Type`: `audio/wav` 或 `audio/pcm`
- `Content-Disposition`: `attachment; filename=speech.wav`
- Body：**二进制音频数据**（WAV PCM16 24kHz mono）

#### curl 示例

```bash
# 基础 TTS
curl -X POST $BASE/v1/audio/speech \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{"model": "kokoro-tts", "input": "你好，世界", "voice": "zf_xiaobei"}' \
  --output speech.wav

# 男声 + 慢速
curl -X POST $BASE/v1/audio/speech \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{"model": "kokoro-tts", "input": "今天天气真不错", "voice": "zm_yunjian", "speed": 0.8}' \
  --output speech.wav

# PCM 原始格式
curl -X POST $BASE/v1/audio/speech \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{"model": "kokoro-tts", "input": "测试", "response_format": "pcm"}' \
  --output speech.pcm
```

---

## 8. VAD 语音活动检测

### `POST /v1/voice/vad`（JSON）

检测音频中是否包含人声以及各语音段的时间范围。Silero VAD 引擎。

#### 请求

```json
{
  "audio": "<base64 编码的 WAV 音频>",
  "threshold": 0.5
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `audio` | string | 是 | - | base64 编码的 WAV |
| `threshold` | float | 否 | 0.5 | 语音概率阈值（0.0-1.0） |

#### 响应

```json
{
  "is_speech": true,
  "speech_prob": 0.95,
  "segments": [
    {"start_ms": 100, "end_ms": 2000, "speech_prob": 0.95},
    {"start_ms": 3500, "end_ms": 5000, "speech_prob": 0.88}
  ],
  "duration_ms": 5000
}
```

### `POST /v1/voice/vad/upload`（表单上传）

```bash
curl -X POST $BASE/v1/voice/vad/upload \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@audio.wav" \
  -F "threshold=0.5"
```

---

## 9. 声纹识别

### 9.1 注册声纹 `POST /v1/voice/speakers`

```json
// 请求（JSON + base64 音频，10-30 秒）
{
  "audio": "<base64 编码的 WAV>",
  "speaker_id": null
}

// 响应
{
  "speaker_id": "spk_abc123",
  "quality_score": 0.92,
  "created": true
}
```

也可通过表单上传：`POST /v1/voice/speakers/upload`

### 9.2 声纹匹配 `POST /v1/voice/speakers/identify`

```json
// 请求
{
  "audio": "<base64 编码的 WAV>",
  "threshold": 0.6
}

// 响应
{
  "identified": true,
  "speaker_id": "spk_abc123",
  "confidence": 0.87
}
```

### 9.3 声纹列表 `GET /v1/voice/speakers`

```json
// 响应
[
  {
    "speaker_id": "spk_abc123",
    "created_at": "2026-01-15T10:30:00Z"
  }
]
```

### 9.4 删除声纹 `DELETE /v1/voice/speakers/{speaker_id}`

```bash
curl -X DELETE $BASE/v1/voice/speakers/spk_abc123 \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"
```

---

## 10. 文档解析

### 10.1 创建解析任务 `POST /v1/documents/parse`

```bash
curl -X POST $BASE/v1/documents/parse \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@document.pdf" \
  -F "model=qwen2.5-vl"
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | binary | 是 | PDF 或 DOCX（≤ 10MB, ≤ 200 页） |
| `model` | string | **是** | VL 模型：`qwen2.5-vl` / `minicpm-v` / `minicpm-o` / `glm-ocr` |
| `pages` | string | 否 | 页码范围，如 `"1-5,10,15-20"` |

返回 `202 Accepted`：

```json
{
  "task_id": "task_abc123",
  "status": "pending"
}
```

### 10.2 查询任务状态 `GET /v1/documents/tasks/{task_id}`

```json
{
  "task_id": "task_abc123",
  "file_name": "report.pdf",
  "total_pages": 50,
  "status": "processing",
  "progress": {"current": 10, "total": 50},
  "model": "qwen2.5-vl"
}
```

`status` 取值：`pending` → `processing` → `completed` / `failed`

### 10.3 获取结果 `GET /v1/documents/tasks/{task_id}/result`

返回解析后的 Markdown 文本。可选 `?format=json` 获取结构化 JSON。

---

## 11. 安全护栏

护栏**仅作用于聊天推理** `/v1/chat/completions`，其他端点（ASR/TTS/VAD/文档解析）不经过护栏。

### 11.1 三级护栏

| 级别 | `guardrails_level` | 检测内容 | 延迟 | 适用场景 |
|------|-------------------|----------|------|---------|
| 快速 | `fast` | Prompt 注入、PII 检测、有害内容 | < 10ms | 实时对话 |
| 标准 | `standard` | fast + NeMo 越狱检测 + YARA 代码注入 | < 70ms | 常规应用 |
| 深度 | `deep` | standard + 幻觉检测 + Tavily 事实核查 | < 30s | 关键决策 |

### 11.2 使用方式

```json
{
  "model": "qwen3-8b",
  "messages": [...],
  "guardrails_enabled": true,
  "guardrails_level": "standard",
  "include_usage": true
}
```

- `guardrails_enabled: false` — 完全跳过护栏
- `include_usage: true` — 响应中包含 `warnings` 和 `guardrails_metadata`

### 11.3 护栏拦截行为

**硬拦截**（返回 403 + 错误码，阻断请求）：
- `E4001` — Prompt 注入检测
- `E4002` — PII 检测
- `E4003` — 有害内容
- `E4005` — 越狱攻击
- `E4006` — 代码注入

**软拦截**（返回结果 + warnings 字段，不中断）：
- 幻觉检测
- 事实核查失败

---

## 12. 模型管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 获取所有模型列表及状态 |
| `/v1/models/{model_id}` | GET | 获取单个模型详情 |
| `/v1/models/load` | POST | 加载指定模型 |
| `/v1/models/unload` | POST | 卸载指定模型 |

```bash
# 查看可用模型
curl $BASE/v1/models \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"

# 加载模型
curl -X POST $BASE/v1/models/load \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-8b"}'
```

---

## 13. 健康检查与监控

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 否 | 完整健康检查（网关 + 模型 + Redis + DB） |
| `/health/live` | GET | 否 | 存活探针 |
| `/health/ready` | GET | 否 | 就绪探针 |
| `/health/gpu` | GET | 否 | GPU 显存详情 |
| `/metrics` | GET | 否 | Prometheus 指标 |

```bash
# 快速检测网关是否存活
curl $BASE/health/live
# {"status": "alive"}

# 完整健康检查
curl $BASE/health
```

---

## 14. 错误码参考

| 错误码 | HTTP | 说明 | 建议处理 |
|--------|------|------|---------|
| **通用** | | | |
| E1000 | 500 | 内部错误 | 重试或联系运维 |
| E1001 | 400 | 参数验证失败 | 检查请求体 |
| E1002 | 429 | 速率限制 | 退避重试 |
| **认证** | | | |
| E2001 | 401 | 未授权 | 检查 Authorization header |
| E2003 | 401 | API Key 无效 | 核实 API Key |
| **模型** | | | |
| E3001 | 404 | 模型不存在 | 检查模型 ID |
| E3002 | 503 | 模型不可用 | 等待模型加载或切换模型 |
| E3003 | 504 | 模型超时 | 减小 max_tokens 或重试 |
| E3004 | 400 | 上下文超长 | 缩减输入长度 |
| **安全护栏** | | | |
| E4001 | 403 | Prompt 注入 | 修改输入内容 |
| E4002 | 403 | PII 检测 | 去除敏感信息 |
| E4003 | 403 | 有害内容 | 修改输入内容 |
| E4005 | 403 | 越狱攻击 | — |
| E4006 | 403 | 代码注入 | — |
| **语音** | | | |
| E7001 | 400 | 音频格式不支持 | 检查 WAV 格式（PCM16/16kHz/mono） |
| E7002 | 400 | 音频超大小/时长 | 缩短音频或压缩 |
| E7003 | 404 | 声纹不存在 | 检查 speaker_id |
| E7004 | 503 | 语音服务不可用 | 服务降级中，稍后重试 |

错误响应格式：

```json
{
  "error": {
    "code": "E3002",
    "message": "Model qwen3-8b is not available",
    "type": "model_unavailable"
  }
}
```

---

## 15. 音频格式要求

所有音频类端点（ASR/VAD/声纹）共享以下强制要求：

| 约束 | 值 |
|------|-----|
| 格式 | WAV (RIFF/WAVE) |
| 编码 | PCM 16-bit signed |
| 采样率 | 16,000 Hz |
| 声道 | 1（单声道） |
| 最大文件 | 10 MB |
| 最大时长 | 60 秒（ASR 最长 300 秒） |

**TTS 输出格式**：WAV PCM16 **24,000 Hz** mono（注意与输入不同）

**服务端验证**：RIFF 魔数 + WAVE 标识 + fmt chunk + WAV bomb 防护。格式不符返回 `E7001`。

---

## 16. 对接最佳实践

### 16.1 LinChat 语音交互管道（推荐编排）

```
方式 A：全 REST（录完再处理）
用户说话
  → LinChat 录音（完整录制）
    → VAD 检测（POST /v1/voice/vad）— 有人声才继续
      → 声纹识别（POST /v1/voice/speakers/identify）— 可选
        → ASR 转文字（POST /v1/audio/transcriptions）
          → LLM 对话（POST /v1/chat/completions）
            → TTS 合成语音（POST /v1/audio/speech）
              → 播放给用户

方式 B：ASR WebSocket 流式（边录边转录，🔴 推荐）
LinChat 建立 WebSocket 长连接（WS /v1/audio/transcriptions/stream）
  → configure auto_commit=true
  → 持续发送麦克风 PCM 帧
    → VAD 内置过滤：噪音/静音自动丢弃，人声自动缓存
      → speech_end → 自动触发 ASR 转录
        → 收到 transcription.completed → 拿到文字
          → LLM 对话（POST /v1/chat/completions）
            → TTS 合成语音（POST /v1/audio/speech）
              → 播放给用户

方式 C：WebSocket VAD + REST ASR（分离 VAD 和 ASR）
LinChat 建立 WebSocket 长连接（WS /v1/voice/vad/stream）
  → 持续发送麦克风 PCM 帧
    → 收到 speech_start → 开始缓存音频
      → 收到 speech_end → 将缓存音频发送 ASR
        → ASR 转文字（POST /v1/audio/transcriptions）
          → LLM 对话（POST /v1/chat/completions）
            → TTS 合成语音（POST /v1/audio/speech）
              → 播放给用户
```

> **方式 B 推荐**：VAD + ASR 合为一个 WebSocket 连接，无需客户端缓存音频，延迟最低。
> 方式 C 适用于需要在客户端侧控制音频缓存和声纹识别的场景。

> **所有编排逻辑在 LinChat 侧实现**，LLM Gateway 只提供原子化的基础模型服务。

### 16.2 超时设置建议

| 端点 | 建议超时 | 说明 |
|------|---------|------|
| 聊天推理（非流式） | 120s | 长文本生成可能较慢 |
| 聊天推理（流式） | 首 chunk 30s | 之后持续读取 SSE |
| ASR（REST） | 30s | CPU 推理，音频越长越慢 |
| ASR（WebSocket） | 连接保持 | 长连接，持续收发，转录事件实时到达 |
| TTS | 30s | GPU 推理，文本越长越慢 |
| VAD（REST） | 5s | 极快 |
| VAD（WebSocket） | 连接保持 | 长连接，持续收发 |
| 声纹匹配 | 10s | 含数据库查询 |
| 文档解析（提交） | 10s | 仅提交任务 |
| 模型加载 | 180s | 需要加载 GPU 权重 |

### 16.3 重试策略

- **可重试**：E1000（内部错误）、E3003（超时）、E7004（服务不可用）
- **不可重试**：E4xxx（安全拦截）、E2xxx（认证失败）、E1001（参数错误）
- **推荐**：指数退避 + 随机抖动，最多 3 次，最大延迟 30 秒

### 16.4 连接保活

LinChat 通过 frpc 隧道连接，建议：

- HTTP 连接使用 Keep-Alive
- 设置合理的 idle timeout（建议 60s）
- 流式响应期间保持长连接，监听 `data: [DONE]` 结束标记

---

> **联系方式**：如有对接问题，请联系安琳。
