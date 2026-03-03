# TTS 流式 WebSocket API 文档

> LLM Gateway 流式文字转语音接口
> 版本: 1.0.0 | 更新: 2026-03-02

## 概述

WebSocket 端点实现**文本流式输入 + 音频流式输出**，客户端可逐 token 发送文本增量，服务端实时返回 PCM16 音频流。适用于 LLM 生成文本的实时语音合成场景（边生成边播放）。

**核心优势**：
- 首音频延迟低（句级分句，不等全部文本）
- 流式双向通信（文本输入 / 音频输出同时进行）
- 自动分句合成（句号/问号等标点触发，逗号在 30 字符后触发）

## 连接

```
ws://<host>:8081/v1/audio/speech/stream?api_key=<API_KEY>
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_key` | string | 是 | API 密钥，通过 query 参数传递 |

### 认证

连接建立后服务端立即校验 `api_key`：
- **有效**：返回 `session.created` 事件
- **无效/缺失**：关闭连接，code=`4001`，reason=`"unauthorized"`
- **TTS 服务不可用**：关闭连接，code=`4002`，reason=`"tts service unavailable"`

## 音频格式

| 属性 | 值 |
|------|-----|
| 编码 | PCM16 (signed 16-bit little-endian) |
| 采样率 | 24000 Hz |
| 声道 | 单声道 (mono) |
| 帧格式 | 原始 PCM bytes（无 WAV 头） |

> 客户端接收到 Binary 帧后可直接写入 PCM 播放器，或拼接后加 WAV 头保存为文件。

## 协议

### 客户端 → 服务端（Text 帧，JSON）

#### 1. `config` — 配置声音和语速（可选）

在发送文本前设置声音和语速。不发送则使用默认值。

```json
{
  "type": "config",
  "voice": "zf_xiaobei",
  "speed": 1.0
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `voice` | string | `"zf_xiaobei"` | 声音名称，见下方可选声音列表 |
| `speed` | float | `1.0` | 语速倍率，范围 0.5 ~ 2.0 |

**可选声音**：

| 声音 ID | 性别 | 说明 |
|---------|------|------|
| `zf_xiaobei` | 女 | 默认声音 |
| `zf_xiaoyi` | 女 | — |
| `zf_xiaoni` | 女 | — |
| `zf_xiaowan` | 女 | — |
| `zf_xiaoyun` | 女 | — |
| `zm_yunjian` | 男 | — |
| `zm_yunxi` | 男 | — |
| `zm_yunxia` | 男 | — |
| `zm_yunyang` | 男 | — |

> 声音 ID 前缀：`zf_` = 中文女声，`zm_` = 中文男声

#### 2. `text.delta` — 文本增量输入

逐步发送待合成文本（模拟 LLM 逐 token 输出）。

```json
{
  "type": "text.delta",
  "delta": "你好，"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `delta` | string | 文本增量片段 |

**分句策略**（服务端自动执行）：
- 遇到句尾标点（`。！？；!?;` 或换行）→ 立即切割合成
- 积累超过 30 字符且遇到逗号（`，、,：:`）→ 切割合成
- 积累超过 200 字符 → 强制切割合成

#### 3. `text.done` — 文本输入完毕

通知服务端所有文本已发送完毕。服务端会 flush 剩余缓冲区文本，合成完成后返回 `audio.done`。

```json
{
  "type": "text.done"
}
```

### 服务端 → 客户端

#### `session.created` — 会话建立（Text 帧）

连接认证成功后立即发送。

```json
{
  "type": "session.created",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "sample_rate": 24000
}
```

#### `tts.sentence_start` — 句子合成开始（Text 帧）

```json
{
  "type": "tts.sentence_start",
  "sentence_idx": 0,
  "text": "你好，世界。"
}
```

#### Binary 帧 — PCM16 音频数据

紧随 `tts.sentence_start` 后，服务端发送一个或多个 Binary 帧，内容为原始 PCM16 音频 bytes。

#### `tts.sentence_end` — 句子合成结束（Text 帧）

```json
{
  "type": "tts.sentence_end",
  "sentence_idx": 0
}
```

#### `audio.done` — 全部合成完毕（Text 帧）

`text.done` 后所有文本合成完毕时发送。客户端收到此事件后可关闭连接。

```json
{
  "type": "audio.done"
}
```

#### `error` — 错误通知（Text 帧）

非致命错误，连接不会关闭，客户端可继续发送。

```json
{
  "type": "error",
  "message": "不支持的声音 'xxx'，可选: zf_xiaobei, zm_yunjian, ..."
}
```

可能的错误：
- 无效声音名称
- 帧过大（> 4KB）
- 无效 JSON
- TTS 合成失败

## 完整时序

```
Client                              Server
  │                                    │
  │──── WebSocket Connect ────────────▶│
  │                                    │  验证 api_key
  │◀── session.created ──────────────│
  │     {session_id, sample_rate}      │
  │                                    │
  │──── config ──────────────────────▶│  (可选)
  │     {voice, speed}                 │
  │                                    │
  │──── text.delta "你好，" ─────────▶│
  │──── text.delta "世界。" ─────────▶│  分句器: "你好，世界。"
  │                                    │
  │◀── tts.sentence_start ───────────│  句0 开始
  │     {idx:0, text:"你好，世界。"}    │
  │◀── Binary PCM chunk ─────────────│  音频数据
  │◀── Binary PCM chunk ─────────────│  音频数据
  │◀── tts.sentence_end ─────────────│  句0 结束
  │                                    │
  │──── text.delta "今天天气" ────────▶│
  │──── text.delta "真好。" ─────────▶│  分句器: "今天天气真好。"
  │                                    │
  │◀── tts.sentence_start ───────────│  句1 开始
  │◀── Binary PCM chunk ─────────────│
  │◀── tts.sentence_end ─────────────│  句1 结束
  │                                    │
  │──── text.done ───────────────────▶│  flush 剩余缓冲
  │◀── audio.done ───────────────────│  全部完成
  │                                    │
  │──── Close ───────────────────────▶│
```

## 调用示例

### Python (websockets)

```python
import asyncio
import json
import struct
import wave

import websockets


async def tts_stream():
    uri = "ws://localhost:8081/v1/audio/speech/stream?api_key=sk-YOUR-API-KEY"

    async with websockets.connect(uri) as ws:
        # 1. 等待 session.created
        event = json.loads(await ws.recv())
        assert event["type"] == "session.created"
        sample_rate = event["sample_rate"]  # 24000
        print(f"Session: {event['session_id']}, SR: {sample_rate}")

        # 2. 配置声音（可选）
        await ws.send(json.dumps({
            "type": "config",
            "voice": "zf_xiaobei",
            "speed": 1.0,
        }))

        # 3. 模拟 LLM 逐 token 输出
        tokens = ["你好", "，", "世界", "。", "今天", "天气", "真好", "。"]
        for token in tokens:
            await ws.send(json.dumps({
                "type": "text.delta",
                "delta": token,
            }))
            await asyncio.sleep(0.05)  # 模拟 LLM 生成间隔

        # 4. 通知文本输入完毕
        await ws.send(json.dumps({"type": "text.done"}))

        # 5. 接收音频流
        pcm_data = bytearray()
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                pcm_data.extend(msg)
            else:
                event = json.loads(msg)
                print(f"Event: {event['type']}", end="")
                if event["type"] == "tts.sentence_start":
                    print(f" — \"{event['text']}\"")
                elif event["type"] == "audio.done":
                    print()
                    break
                else:
                    print()

        # 6. 保存为 WAV 文件
        with wave.open("output.wav", "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)

        duration = len(pcm_data) / 2 / sample_rate
        print(f"Saved: output.wav ({duration:.2f}s, {len(pcm_data)} bytes)")


asyncio.run(tts_stream())
```

### Python — 与 LLM 流式输出集成

```python
import asyncio
import json

import httpx
import websockets


async def llm_to_tts():
    """LLM 流式生成 → TTS 流式合成 → 实时播放"""

    # 建立 TTS WebSocket 连接
    ws = await websockets.connect(
        "ws://localhost:8081/v1/audio/speech/stream?api_key=sk-YOUR-API-KEY"
    )
    event = json.loads(await ws.recv())  # session.created
    await ws.send(json.dumps({"type": "config", "voice": "zm_yunjian"}))

    # 启动音频接收协程
    pcm_buffer = bytearray()
    done_event = asyncio.Event()

    async def receive_audio():
        nonlocal pcm_buffer
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                pcm_buffer.extend(msg)
                # 这里可以实时推送给播放器
            else:
                ev = json.loads(msg)
                if ev["type"] == "audio.done":
                    done_event.set()
                    break

    recv_task = asyncio.create_task(receive_audio())

    # LLM 流式请求
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "http://localhost:8081/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-YOUR-API-KEY",
                "Content-Type": "application/json",
            },
            json={
                "model": "qwen3-8b",
                "messages": [{"role": "user", "content": "讲一个短笑话"}],
                "max_tokens": 200,
                "stream": True,
            },
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {}).get("content", "")
                if delta:
                    # 实时发送给 TTS
                    await ws.send(json.dumps({
                        "type": "text.delta",
                        "delta": delta,
                    }))

    # 通知 TTS 文本输入完毕
    await ws.send(json.dumps({"type": "text.done"}))
    await done_event.wait()
    await ws.close()

    print(f"Total audio: {len(pcm_buffer) / 2 / 24000:.2f}s")


asyncio.run(llm_to_tts())
```

### JavaScript (浏览器)

```javascript
const API_KEY = "sk-YOUR-API-KEY";
const ws = new WebSocket(
  `ws://localhost:8081/v1/audio/speech/stream?api_key=${API_KEY}`
);

const audioCtx = new AudioContext({ sampleRate: 24000 });
const pcmChunks = [];

ws.onmessage = (event) => {
  if (event.data instanceof Blob) {
    // Binary 帧: PCM16 音频
    event.data.arrayBuffer().then((buf) => {
      pcmChunks.push(new Int16Array(buf));
      // 可在此处实时播放（通过 AudioWorklet 或 ScriptProcessor）
    });
  } else {
    // Text 帧: JSON 事件
    const ev = JSON.parse(event.data);
    console.log("Event:", ev.type, ev);

    if (ev.type === "session.created") {
      // 配置声音
      ws.send(JSON.stringify({ type: "config", voice: "zf_xiaobei" }));

      // 发送文本
      const text = "你好，世界。今天天气真好。";
      for (const char of text) {
        ws.send(JSON.stringify({ type: "text.delta", delta: char }));
      }
      ws.send(JSON.stringify({ type: "text.done" }));
    }

    if (ev.type === "audio.done") {
      console.log("All audio received, total chunks:", pcmChunks.length);
      playPCM(pcmChunks, audioCtx);
    }
  }
};

function playPCM(chunks, ctx) {
  // 合并所有 PCM16 chunks
  const totalSamples = chunks.reduce((sum, c) => sum + c.length, 0);
  const buffer = ctx.createBuffer(1, totalSamples, 24000);
  const channel = buffer.getChannelData(0);
  let offset = 0;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++) {
      channel[offset++] = chunk[i] / 32768; // int16 → float32
    }
  }
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.start();
}
```

## PCM → WAV 转换

收到的 Binary 帧是原始 PCM16 数据（无文件头）。保存为 WAV 文件时需手动添加 44 字节 WAV 头：

```python
import struct

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000) -> bytes:
    """原始 PCM16 bytes → 完整 WAV 文件 bytes"""
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16,
        b"data", data_size,
    )
    return header + pcm_data
```

## 限制

| 约束 | 值 |
|------|-----|
| 单文本帧最大 | 4096 bytes (4KB) |
| 语速范围 | 0.5 ~ 2.0 |
| 单句最大字符 | 4096 字符（TTS 服务层限制） |
| 强制分句阈值 | 200 字符 |

## 错误码

| 关闭码 | 含义 |
|--------|------|
| `4001` | 认证失败（api_key 无效或缺失） |
| `4002` | TTS 服务不可用 |

| 事件错误 | 含义 |
|----------|------|
| `"frame too large"` | 文本帧超过 4KB |
| `"invalid JSON"` | JSON 解析失败 |
| `"不支持的声音 'xxx'"` | 声音不在白名单中 |
| `"TTS 合成失败: ..."` | Kokoro 合成引擎错误 |
