# Implementation Plan: 语音模块迁移 — Gateway WebSocket → ASR 流式转录 + Agent Pipeline + TTS

**Branch**: `010-voice-agent-pipeline` | **Date**: 2026-03-02 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-voice-agent-pipeline/spec.md`

## Summary

将语音模块从已废弃的 Gateway WebSocket 全代理模式迁移为：**Gateway ASR WebSocket 流式转录（内置 VAD）→ 完整 LangGraph Agent Pipeline → TTS 流式 WebSocket** 编排模式。核心变更：替换 `gateway_client.py` 为 ASR 流式客户端，将 Agent 推理从 Gateway 侧移到 LinChat 侧（复用 `AgentService.execute()`），新增 TTS 流式 WebSocket 客户端（`TTSStreamClient`，Agent content chunk 直接作为 `text.delta` 送入 Gateway TTS WS，Gateway 自动分句合成返回 PCM 音频流），删除 enriched 独立代码路径。

**设计参考**: [CleanS2S](https://github.com/opendilab/CleanS2S) 的线性管道架构 — recv → STT → LLM → TTS → send，保持核心流水线简洁。

**MVP 策略**: Phase 1-4 为 MVP（配置→基础服务→Consumer 接入→VoicePipeline 闭环），先跑通语音对话核心功能。Phase 5（持久化）/ Phase 6（持续监听）/ Phase 7（清理）为 Post-MVP 增量交付。

## Technical Context

**Language/Version**: Python 3.11+ (后端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, channels (WebSocket), websockets (Gateway ASR WS 客户端 + TTS 流式 WS 客户端), LangGraph, LangChain, Langfuse
**Storage**: PostgreSQL 15 (Message, MediaAttachment, LangGraphExecution), Redis (语音会话状态/音频帧缓存/频率限制), MinIO (音频文件)
**Testing**: pytest + pytest-django + pytest-asyncio
**Target Platform**: Linux server (ASGI)
**Project Type**: web (后端 only，前端零修改)
**Performance Goals**: 语音停止到首字符 < 5s (SC-003), TTS 首音频帧 < 2s (SC-004, 流式合成不等整句), 取消 < 1s (SC-009)
**Constraints**: 单用户系统，无并发会话，Gateway 通过 frpc-visitor `127.0.0.1:8100` 访问
**Scale/Scope**: 单用户，不考虑并发

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 合规状态 | 说明 |
|------|------|---------|------|
| 1.1 分层架构 | 视图→服务→数据层 | ✅ | 新增服务在 services/ 目录，Consumer 仅做协议适配 |
| 1.2 接口设计 | WebSocket 协议 | ✅ | 保持现有前端 WebSocket 事件协议不变 (FR-005) |
| 1.3 数据一致性 | PostgreSQL 为主 | ✅ | Message + MediaAttachment 事务写入 |
| 2.1 代码规范 | PEP 8 + Black + 类型注解 | ✅ | 所有新代码遵循 |
| 3.1 测试覆盖率 | 服务层 95% | ✅ | 新增服务全覆盖 |
| 4.1 安全 | user_id 隔离 | ✅ | 所有操作按 user_id 粒度 |
| 4.3 LLM 异常 | 统一处理 | ✅ | AgentService.execute() 内置 LLM 异常处理；ASRStreamClient 连接错误生成 error 事件；TTSStreamClient 连接断开/error 事件 → 降级纯文字回复 |
| 4.4 术语 | 单用户单会话 | ✅ | 无 conversation_id |
| 5.1 性能 | 首令牌 < 2s | ✅ | 语音场景适用宪法"多模态推理首字节 < 5s"豁免条款 (SC-003) |
| 8.2 ASGI | uvicorn | ✅ | 已有 |
| 9.2 并发模型 | 单用户 | ✅ | 不实现并发控制 |

**Gate Result**: ✅ PASS — 无违规

## Project Structure

### Documentation (this feature)

```text
specs/010-voice-agent-pipeline/
├── plan.md              # 本文件
├── spec.md              # 特性规范
├── research.md          # Phase 0: 研究发现
├── data-model.md        # Phase 1: 数据模型（无新增，仅文档化现有模型）
├── contracts/           # Phase 1: 接口契约
│   └── gateway-asr-ws.md  # Gateway ASR WebSocket 事件映射表
└── tasks.md             # Phase 2: 任务清单
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── voice/
│   │   ├── consumers.py              # [修改] 移除 GatewayClient 依赖，使用 ASR 客户端
│   │   ├── consumer_events.py        # [重写] Gateway ASR 事件适配 → 前端协议翻译
│   │   ├── consumer_inference.py     # [重写] 移除 enriched 推理，使用 AgentService
│   │   ├── consumer_session.py       # [修改] 会话管理适配 ASR 客户端
│   │   ├── models.py                 # [不变] 现有模型满足需求
│   │   ├── services/
│   │   │   ├── asr_stream_client.py  # [新增] Gateway ASR WebSocket 流式客户端
│   │   │   ├── tts_stream_client.py   # [新增] TTS 流式 WebSocket 客户端
│   │   │   ├── voice_pipeline.py     # [新增] 语音管道编排（ASR→Agent→TTS）
│   │   │   ├── gateway_client.py     # [删除] 旧 Gateway WebSocket 客户端
│   │   │   ├── voice_session_service.py   # [修改] 移除旧 STT 逻辑
│   │   │   ├── voice_persist_service.py   # [修改] 移除 enriched 推理，简化持久化
│   │   │   ├── voice_context_service.py   # [删除] enriched 上下文（Agent Pipeline 已含）
│   │   │   ├── response_decision_service.py # [不变] 唤醒词 + 决策逻辑
│   │   │   ├── speaker_service.py    # [微调] 声纹管理接口保留，LLM_GATEWAY_HTTP_URL → LLM_GATEWAY_URL
│   │   │   └── device_service.py     # [不变] 设备管理
│   │   └── ...
│   └── ...
└── tests/
    └── voice/
        ├── test_asr_stream_client.py  # [新增] ASR 客户端测试
        ├── test_tts_stream_client.py   # [新增] TTS 流式客户端测试
        ├── test_voice_pipeline.py     # [新增] 语音管道测试
        ├── test_consumers.py          # [修改] 适配新架构
        └── ...
```

**Structure Decision**: 在现有 `backend/apps/voice/` 结构内修改和新增，不新建 Django app。新增 3 个服务文件（asr_stream_client, tts_stream_client, voice_pipeline），删除 2 个过时文件（gateway_client, voice_context_service），修改 5 个文件。

## Architecture Design

### 核心架构变更

```
[旧架构] Gateway 全代理模式
┌─────────┐    PCM     ┌──────────────────┐   事件   ┌─────────────┐
│  前端   │──────────→│ VoiceConsumer    │←─────→│ Gateway     │
│         │←─────────→│ + GatewayClient  │        │ /v1/voice/  │
└─────────┘  事件      └──────────────────┘        │ stream      │
                        Gateway 完成所有：         │ (已删除!)   │
                        VAD + STT + LLM + TTS     └─────────────┘

[新架构] 分离编排模式
┌─────────┐    PCM     ┌──────────────────┐  PCM帧  ┌─────────────┐
│  前端   │──────────→│ VoiceConsumer    │───────→│ Gateway ASR │
│         │←─────────→│ + ASR Client     │←──────│ WS /v1/audio│
└─────────┘  事件      │ + VoicePipeline  │  事件   │ /stream     │
                       │                  │        └─────────────┘
                       │   transcription  │
                       │   .completed     │
                       │       ↓          │
                       │ AgentService     │  (完整 LangGraph Pipeline)
                       │   .execute()     │
                       │       ↓          │
                       │ TTSStreamClient  │───────→ WS /v1/audio/speech/stream
                       │ (流式合成)       │←──────  PCM 音频流
                       └──────────────────┘
```

### 事件翻译层（FR-005）

Gateway ASR WebSocket 事件 → 现有前端协议事件映射：

| Gateway ASR 事件 | 前端协议事件 | 转换逻辑 |
|-----------------|-------------|---------|
| `session.created` | `session.configured` | 提取 session_id，附加 mode/status |
| `vad.speech_start` | `vad.speech_start` | 注入 Consumer 生成的 segment_id |
| `vad.speech_end` | `vad.speech_end` | 注入 segment_id |
| `transcription.completed` | `transcription.complete` | 触发 VoicePipeline |
| `transcription.failed` | `transcription.failed` | 直接转发 |
| `error` | `error` | 映射错误码 |
| — (Agent 流式输出) | `response.start` | VoicePipeline 生成 |
| — (Agent StreamChunk) | `response.delta` | VoicePipeline 转换 |
| — (Agent 完成) | `response.end` | VoicePipeline 生成 |
| — (TTS `tts.sentence_start`) | — (日志记录) | 可选日志：TTS 开始合成第 N 句 |
| — (TTS PCM binary) | WebSocket binary frame | `TTSStreamClient._receive_loop` → `consumer._send_binary()` 直接转发 PCM16 24kHz 音频帧 |
| — (TTS `tts.sentence_end`) | — (日志记录) | 可选日志：TTS 第 N 句合成完毕 |
| — (TTS `audio.done`) | — (内部信号) | 标记 TTS 全部合成完成，VoicePipeline 可发送 `response.end` |

### VoicePipeline 编排流程

```python
# voice_pipeline.py 核心逻辑
# 设计参考：CleanS2S 的线性管道 recv → STT → LLM → TTS → send
# TTS 使用 Gateway 流式 WebSocket（text.delta → 自动分句 → PCM 音频流）
async def run_pipeline(user_id, transcribed_text, segment_id, consumer):
    """ASR 转录完成后的完整编排流程"""

    # 1. 生成 IDs
    request_id = uuid.uuid4().hex
    thread_id = f"user_{user_id}"

    # 2. 注册推理任务（复用 SSE 聊天的 InferenceService 基础设施, FR-008）
    from apps.graph.services.inference_service import InferenceService
    await InferenceService.register_task(user_id, request_id, model="agent")

    # 3. 连接 TTS WS（如果启用）
    tts_client = None
    if settings.VOICE_TTS_ENABLED:
        try:
            tts_client = TTSStreamClient(
                on_audio=consumer._send_binary,     # PCM → 前端
                on_sentence_start=lambda idx, text:
                    logger.debug("TTS sentence %d: %s", idx, text),
            )
            await tts_client.connect()
            await tts_client.configure(voice=settings.VOICE_TTS_VOICE)
        except Exception:
            logger.warning("TTS WS 连接失败，降级为纯文字")
            tts_client = None

    # 4. 发送 response.start
    response_id = f"voice_{request_id[:16]}"
    await consumer.send_event("response.start", {"response_id": response_id})

    # 5. 流式 Agent → 流式 TTS（Gateway 自动分句合成，无需客户端 split_sentences）
    try:
        async for chunk in AgentService.execute(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            user_message=transcribed_text
        ):
            # AgentService 内部 stop_event 由 InferenceService.cancel_task() 触发
            if chunk.type == "content":
                # 5a. 转发文字到前端
                await consumer.send_event("response.delta", {
                    "delta": {"content": chunk.content},
                    "response_id": response_id
                })
                # 5b. 文字 → TTS WS（Gateway 自动分句合成并通过 _receive_loop 回调返回 PCM 帧）
                if tts_client and tts_client._connected:
                    await tts_client.send_text_delta(chunk.content)

            elif chunk.type == "interrupted":
                break  # 用户取消或 barge-in

            elif chunk.type == "done":
                pass  # 循环结束后统一处理

            elif chunk.type == "error":
                # 宪法 4.3 LLM 异常处理
                # StreamChunk 类型定义见 apps/chat/services/types.py
                # data: Optional[dict] 含 gateway_error/content_control 等附加信息
                await consumer.send_event("error", {...})
    finally:
        # 6. 通知 TTS 文本结束，等待音频全部合成
        if tts_client and tts_client._connected:
            try:
                await tts_client.send_text_done()
                await tts_client.wait_for_done(timeout=30)
            except Exception:
                logger.warning("TTS flush 超时")
            finally:
                await tts_client.disconnect()

    # 7. 发送 response.end
    await consumer.send_event("response.end", {
        "response_id": response_id,
        "usage": {...}
    })

    # 8. 持久化音频附件（Phase 5 T016 实现）
    # await persist_audio_attachment(user_id, segment_id, request_id)
    # ⚠️ 不发送前端事件 — spec Scope 禁止新增 WebSocket 事件类型，仅记录 INFO 日志

async def cancel(self, user_id: int):
    """取消正在进行的语音推理 — 复用 SSE 聊天取消机制（FR-008, SC-009）"""
    from apps.graph.services.inference_service import InferenceService
    success, rid = await InferenceService.cancel_task(user_id)
    # InferenceService.cancel_task 内部会：
    # 1. 删除 user:{uid}:inference_task Redis 键
    # 2. signal_stop(request_id) → 设置进程内 stop_event（AgentService 检查）
    # 3. Pub/Sub 广播 inference_cancel 事件
    return success
```

### 数据流时序

```
Agent StreamChunk(content="你好")
  → consumer: response.delta {"content": "你好"}     (前端看到文字)
  → tts_client: text.delta "你好"                     (送入 TTS WS)

Agent StreamChunk(content="，世界。")
  → consumer: response.delta {"content": "，世界。"}
  → tts_client: text.delta "，世界。"

  Gateway TTS 分句器检测到句号 → 合成 "你好，世界。"
  → tts_client._receive_loop: tts.sentence_start
  → tts_client._receive_loop: binary PCM → consumer._send_binary()  (前端收到音频)
  → tts_client._receive_loop: tts.sentence_end

Agent StreamChunk(done)
  → tts_client: text.done
  → Gateway flush 剩余缓冲
  → tts_client._receive_loop: audio.done
  → consumer: response.end
```

### 消息持久化策略

**变更点**：`AgentService.execute()` 内部已处理 Message 创建（首 token 时创建 user + assistant Message）。流式 TTS 返回 PCM16 24kHz（无 WAV 头），持久化时需在 `_receive_loop` 中除转发前端外同时累积 PCM bytes，pipeline 结束后用 `pcm_to_wav()` 转换为 WAV 再上传 MinIO。但语音模式需要：

1. **User Message**: 需要额外设置 `is_voice=True` + 关联音频附件
2. **Assistant Message**: `AgentService.execute()` 自动创建，无需额外处理

**方案**：在 `AgentService.execute()` 返回后，补充更新 user Message 的语音标记和音频附件关联。

```python
# VoicePipeline 持久化补充
async def persist_audio_attachment(user_id, segment_id, request_id):
    """持久化语音附件 — 事务保护，失败回滚（宪法 1.3）"""
    # 1. 先完成存储操作（可回滚：删除已上传文件）
    pcm_chunks = await voice_session_service.get_audio_chunks(user_id, segment_id)
    wav_bytes = merge_pcm_to_wav(pcm_chunks)
    storage_path = f"media/{user_id}/{date}/{uuid}.wav"
    await minio_service.upload_bytes(storage_path, wav_bytes)

    try:
        # 2. 数据库操作在事务内完成
        async with transaction.atomic():
            user_msg = await message_repo.get_by_request_id(
                user_id=user_id, request_id=request_id, role="user"
            )
            user_msg.is_voice = True
            await message_repo.update(user_msg)

            await media_attachment_repo.create(MediaAttachment(
                message=user_msg,
                media_type="audio",
                storage_path=storage_path,
                duration_seconds=calculate_duration(pcm_chunks),
                file_size=len(wav_bytes),
                expires_at=now + MEDIA_EXPIRY_DAYS
            ))

            assistant_msg = await message_repo.get_by_request_id(
                user_id=user_id, request_id=request_id, role="assistant"
            )
            assistant_msg.is_voice = True
            await message_repo.update(assistant_msg)
    except Exception:
        # 事务回滚后清理已上传的 MinIO 文件
        await minio_service.delete(storage_path)
        raise
```

### ASR Stream Client 设计

```python
# asr_stream_client.py
class ASRStreamClient:
    """Gateway ASR WebSocket 流式客户端"""

    def __init__(self, on_event: Callable):
        self._ws = None
        self._connected = False
        self._on_event = on_event  # 事件回调
        self._api_key = settings.LLM_GATEWAY_API_KEY  # ASR WS + TTS WS 共用

    async def connect(self):
        """建立 ASR WebSocket 连接"""
        url = f"ws://127.0.0.1:8100/v1/audio/transcriptions/stream?api_key={self._api_key}"
        self._ws = await websockets.connect(url)
        # 等待 session.created
        session_event = json.loads(await self._ws.recv())
        self._session_id = session_event["session_id"]
        self._connected = True
        # 启动接收循环
        asyncio.create_task(self._receive_loop())

    async def configure(self, auto_commit=True, speech_pad_ms=2000, language="auto"):
        """配置 ASR 参数"""
        await self._ws.send(json.dumps({
            "type": "configure",
            "auto_commit": auto_commit,
            "speech_pad_ms": speech_pad_ms,
            "language": language
        }))

    async def send_audio(self, pcm_data: bytes):
        """转发 PCM 音频帧"""
        await self._ws.send(pcm_data)

    async def disconnect(self):
        """关闭连接"""
        if self._ws:
            await self._ws.close()
            self._connected = False

    async def _receive_loop(self):
        """接收 Gateway 事件并回调"""
        try:
            async for message in self._ws:
                if isinstance(message, str):
                    event = json.loads(message)
                    await self._on_event(event)
        except websockets.exceptions.ConnectionClosed as e:
            # FR: 连接断开 → 立即终止，不重连
            await self._on_event({
                "type": "error",
                "message": f"ASR 连接断开: {e.code}",
                "code": "CONNECTION_CLOSED"
            })
```

### TTSStreamClient 设计

```python
# tts_stream_client.py
class TTSStreamClient:
    """Gateway TTS 流式 WebSocket 客户端

    生命周期: 每次 VoicePipeline.run_pipeline() 创建一个实例，pipeline 结束后关闭。
    Gateway 自动分句合成（句号/问号立即切 + 逗号 30 字符后切 + 200 字符强制切），
    客户端无需实现 split_sentences() 分句逻辑。
    """

    def __init__(self, on_audio: Callable[[bytes], Awaitable],
                       on_sentence_start: Callable[[int, str], Awaitable] | None = None,
                       on_done: Callable[[], Awaitable] | None = None):
        self._ws = None
        self._on_audio = on_audio          # 回调: PCM binary → consumer._send_binary()
        self._on_sentence_start = on_sentence_start  # 可选: 日志
        self._on_done = on_done            # 回调: audio.done → 标记完成
        self._api_key = settings.LLM_GATEWAY_API_KEY
        self._connected = False
        self._done_event = asyncio.Event()

    async def connect(self) -> str:
        """建立 TTS WS 连接，返回 session_id"""
        url = f"{settings.VOICE_TTS_URL}?api_key={self._api_key}"
        self._ws = await websockets.connect(url)
        event = json.loads(await self._ws.recv())  # session.created
        assert event["type"] == "session.created"
        self._session_id = event["session_id"]
        self._sample_rate = event["sample_rate"]  # 24000
        self._connected = True
        self._recv_task = asyncio.create_task(self._receive_loop())
        return self._session_id

    async def configure(self, voice: str = None, speed: float = None):
        """配置声音和语速（可选）"""
        msg = {"type": "config"}
        if voice: msg["voice"] = voice
        if speed: msg["speed"] = speed
        await self._ws.send(json.dumps(msg))

    async def send_text_delta(self, text: str):
        """发送文本增量（Agent 每个 content chunk 调用一次）"""
        await self._ws.send(json.dumps({"type": "text.delta", "delta": text}))

    async def send_text_done(self):
        """通知文本输入完毕，等待 Gateway flush 剩余缓冲"""
        await self._ws.send(json.dumps({"type": "text.done"}))

    async def wait_for_done(self, timeout: float = 30.0):
        """等待 audio.done 信号"""
        await asyncio.wait_for(self._done_event.wait(), timeout=timeout)

    async def disconnect(self):
        """关闭连接"""
        if self._ws:
            await self._ws.close()
            self._connected = False

    async def _receive_loop(self):
        """接收 TTS 事件和音频"""
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    await self._on_audio(msg)  # PCM 帧 → 转发前端
                else:
                    event = json.loads(msg)
                    if event["type"] == "tts.sentence_start":
                        if self._on_sentence_start:
                            await self._on_sentence_start(
                                event["sentence_idx"], event["text"])
                    elif event["type"] == "audio.done":
                        self._done_event.set()
                        if self._on_done:
                            await self._on_done()
                        break
                    elif event["type"] == "error":
                        logger.warning("TTS error: %s", event["message"])
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("TTS WS 断开: code=%s", e.code)
            self._done_event.set()  # 不阻塞 pipeline
```

> **注意**: 原 `split_sentences()` 函数和 `TTSService` REST 客户端已废弃。Gateway TTS 流式 WebSocket 自带分句合成能力，客户端只需将 Agent content chunk 逐个送入 `text.delta`，Gateway 自动在合适位置切句并返回 PCM 音频流。详见 `docs/tts-websocket-api.md`。

### 两种模式的统一架构

| 阶段 | voice_chat 模式 | continuous_listen 模式 |
|------|----------------|----------------------|
| 连接 | ASR WS (auto_commit=true) | ASR WS (auto_commit=true) |
| VAD | Gateway 内置 | Gateway 内置 |
| 转录 | Gateway 自动 | Gateway 自动 |
| 决策 | 直接进入 Pipeline | response_decision_service.decide() |
| 推理 | AgentService.execute() | 仅 RESPOND → AgentService.execute() |
| TTS | 流式 WS 合成（Gateway 自动分句） | 流式 WS 合成（Gateway 自动分句） |
| 持久化 | Message + 音频附件 | RESPOND: Message + 音频; RECORD_ONLY: 仅 user Message |

### 删除清单

| 删除内容 | 文件 | 原因 |
|---------|------|------|
| `GatewayClient` 类 | `services/gateway_client.py` | 连接已废弃端点，被 ASRStreamClient 替换 |
| `voice_context_service.py` | `services/voice_context_service.py` | enriched 模式专用，Agent Pipeline 已包含上下文构建 |
| `voice_chat_enriched` 模式代码 | `consumer_events.py`, `consumer_inference.py` | FR-011 合并为统一标准模式 |
| `_enriched_voice_inference()` | `consumer_inference.py` | enriched 推理被 VoicePipeline 替代 |
| `_do_enriched()` | `consumer_inference.py` | enriched 推理内部实现 |
| `do_enriched_inference()` | `voice_persist_service.py` | HTTP 直调推理被 AgentService 替代 |
| `_speaker_identified_event` | `consumers.py` | enriched 模式声纹锁 |
| `LLM_GATEWAY_WS_URL` 配置 | `core/settings.py` | 旧 WebSocket 端点 URL |

### 配置变更

```python
# settings.py 新增
VOICE_ASR_WS_URL = "ws://127.0.0.1:8100/v1/audio/transcriptions/stream"
VOICE_TTS_URL = "ws://127.0.0.1:8100/v1/audio/speech/stream"  # WS 流式 TTS（非 HTTP REST）
VOICE_TTS_ENABLED = True
VOICE_TTS_VOICE = "zf_xiaobei"
VOICE_TTS_TIMEOUT = 30  # wait_for_done 超时秒数
VOICE_ASR_SPEECH_PAD_MS = 2000
VOICE_ASR_LANGUAGE = "auto"
VOICE_MAX_SEGMENT_DURATION = 60  # 秒，单段语音最大时长（超时强制 commit 转录）

# settings.py 重命名
# LLM_GATEWAY_WS_API_KEY → LLM_GATEWAY_API_KEY（ASR WS + TTS WS 共用同一个 Gateway Key）

# settings.py 删除
# LLM_GATEWAY_WS_URL    (旧 WebSocket 全代理端点)
# LLM_GATEWAY_HTTP_URL   (仅被 enriched 推理和 STT HTTP 使用，两者均已删除；speaker_service.py 引用已迁移至 LLM_GATEWAY_URL)
```

## Complexity Tracking

无宪法违规需要记录。
