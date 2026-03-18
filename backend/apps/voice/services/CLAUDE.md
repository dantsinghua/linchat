# Voice Services 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> `apps/voice/services/` 语音交互业务逻辑层。

---

## 文件清单

| 文件 | 行数 | 职责 | 全局实例 |
|------|------|------|---------|
| `ws_client_base.py` | 85 | WebSocket 客户端基类（连接/心跳/接收循环/断开），ASR/TTS 共用 | 无 |
| `asr_stream_client.py` | 48 | Gateway ASR WebSocket 流式客户端，继承 `BaseWSClient` | 无（每个 Consumer 创建） |
| `tts_stream_client.py` | 76 | Gateway TTS 流式 WebSocket 客户端，继承 `BaseWSClient` | 无（每次 TTSPipelineManager._play_text 创建） |
| `tts_pipeline_manager.py` | 148 | TTS 播报队列管理器（安慰语音 → Agent 回复 → 错误播报，3 级递进 + cancel） | 无（每次 pipeline 创建） |
| `voice_pipeline.py` | 135 | 语音推理管道编排（Agent→TTS + 持久化 + barge-in 打断） | 无（静态方法 + 类方法） |
| `voice_session_service.py` | 97 | 语音会话生命周期 + Redis 状态 + 音频缓存 + 频率限制（WS/LLM） | `voice_session_service` |
| `voice_persist_service.py` | 136 | PCM→WAV 转换 + MinIO 上传/删除 + record_only_ambient 持久化 | `voice_persist_service` |
| `voice_messages.py` | 27 | 协议消息构建辅助函数（error_msg/response_event/delta_msg/build_agent_error） | 无 |
| `speaker_service.py` | 86 | 声纹注册/删除/识别（对接 Gateway HTTP /v1/voice/speakers） | `speaker_service` |
| `device_service.py` | 76 | 设备注册/Token 管理（SM4 加密/解密匹配） | `device_service` |
| `response_decision_service.py` | 145 | 唤醒词检测 + 响应决策（RESPOND/RECORD_ONLY/STOP）+ LLM 意图分类（仅 ambient） | `response_decision_service` |
| `utterance_aggregator.py` | 97 | 多轮话语聚合器（缓冲 ASR 转录 → 静默超时/满缓冲 → 聚合回调） | 无（每个 ambient Consumer 创建） |
| `tts_router.py` | 56 | 跨设备 TTS 路由（Channels group_send 广播音频帧到浏览器连接） | 无（每次 ambient pipeline 创建） |
| `voice_settings_service.py` | 64 | 语音设置 CRUD（get_or_create + update） | `voice_settings_service` |

---

## 继承关系

```
BaseWSClient (ws_client_base.py)
  ├── ASRStreamClient (asr_stream_client.py)  — 重写 _handle_message() 分发 ASR 事件
  └── TTSStreamClient (tts_stream_client.py)  — 重写 _handle_message() 处理音频/句子/done 事件
```

### BaseWSClient 公共能力

| 方法 | 说明 |
|------|------|
| `_connect_ws(url, **ws_kwargs)` | 建立 WebSocket 连接，等待 session.created，启动 _receive_loop |
| `disconnect()` | 优雅关闭连接 + 取消接收任务 |
| `_send_json_msg(data)` | 发送 JSON 消息 |
| `_send_bytes_msg(data)` | 发送二进制帧 |
| `_receive_loop()` | 接收循环（处理 ConnectionClosed / CancelledError） |
| `_handle_message(msg)` | **抽象方法** — 子类实现消息分发逻辑 |
| `_on_connection_lost(err)` | **可选重写** — 连接丢失回调 |

### cleanup_ws_connection(ws, recv_task) — 模块级函数

独立于 BaseWSClient 的清理函数，安全关闭 WS 连接并取消接收任务。用于异常路径清理。

---

## 服务依赖关系

```
VoiceConsumer（consumers.py + 3 Mixin）
  ├── ASRStreamClient(BaseWSClient) — Gateway ASR WebSocket 通信
  ├── voice_session_service  — 会话状态 / 音频缓存 / 频率限制
  ├── UtteranceAggregator    — [ambient 专用] 多段话语缓冲聚合
  ├── response_decision_service — 唤醒词 + 响应决策（ambient 模式）
  │     ├── voice_settings_repo — PostgreSQL (wake_words)
  │     ├── voice_session_service — Redis (active_conv)
  │     └── model_service — PostgreSQL (LLM 意图分类模型配置，仅 ambient 模式)
  ├── VoicePipeline          — Agent + TTS 编排 + 持久化
  │     ├── AgentService（apps.graph）— LangGraph 流式执行
  │     ├── InferenceService（apps.graph）— 任务注册/取消
  │     ├── TTSPipelineManager — 安慰/回复/错误播报队列 + cancel
  │     │     └── TTSStreamClient(BaseWSClient) — Gateway TTS 流式合成
  │     ├── voice_messages — 协议消息构建（error_msg/delta_msg/response_event）
  │     ├── [ambient] TTSRouter — Channels group_send 跨设备 TTS 广播
  │     ├── voice_persist_service — PCM→WAV + MinIO 上传 + record_only_ambient
  │     ├── voice_session_service — 音频缓存读取/清理
  │     └── message_repo（apps.chat）— Message 创建/更新
  └── speaker_service        — 声纹识别
```

---

## VoicePipeline 编排流程

```
ASR transcription.completed
  → [ambient] EventMixin._handle_ambient_transcription()
    → 停止词预检 → aggregator.add(text) → 等待聚合超时
    → _on_utterance_aggregated() → ResponseDecisionService.decide()
      → RESPOND: run_pipeline(mode=ambient, 跳过内部决策)
      → RECORD_ONLY: voice_persist_service.record_only_ambient() (保存+清理超限消息)
      → STOP: cancel()
  → [voice_chat] 直接进入
  → _run_inner():
    → barge-in 检查: 旧 pipeline 正在运行 → cancel + 等待锁释放（2s 超时）
    → asyncio.Lock 互斥（同一用户同时只有 1 个 pipeline）
    → rate limit check（60次/分）→ InferenceService.register_task()
    → TTSPipelineManager.start()
      → [ambient] on_audio=TTSRouter.get_on_audio_callback(user_id)
      → [其他] on_audio=consumer._send_binary
    → response.start → AgentService.execute() 流式
      → response.delta 仅文字推送前端 + 累积 full_response
      → 异常/中断: stop_comfort_timer + enqueue(error_text, "error")
    → Agent 完成: stop_comfort_timer + enqueue(full_response, "response")
    → wait_idle() → shutdown()
    → [ambient] TTSRouter.send_control(user_id, "tts.completed")
    → response.end
    → voice_persist_service.persist_audio_attachment()（事务: 标记 is_voice + 创建 MediaAttachment）
```

---

## ASRStreamClient（asr_stream_client.py）— 继承 BaseWSClient

| 方法 | 说明 |
|------|------|
| `connect()` | 调用 `_connect_ws()` 建立 ASR WS 连接，返回 session_id |
| `configure()` | 发送配置消息（auto_commit, speech_pad_ms, language） |
| `send_audio(pcm_data)` | 转发 PCM 音频帧（binary） |
| `send_commit()` | 手动触发转录（语音段超时安全网） |
| `_handle_message(msg)` | 解析 JSON → 调用 `_on_event(event)` 回调 |
| `_on_connection_lost(err)` | 调用 `_on_error(err)` 回调 |

---

## TTSStreamClient（tts_stream_client.py）— 继承 BaseWSClient

| 方法 | 说明 |
|------|------|
| `connect()` | 调用 `_connect_ws()` 建立 TTS WS 连接，获取 sample_rate |
| `configure(voice, speed)` | 配置声音和语速 |
| `send_text_delta(text)` | 发送文本增量（Agent 每个 chunk 调用一次） |
| `send_text_done()` | 通知文本输入完毕（Gateway flush 剩余缓冲） |
| `wait_for_done(timeout)` | 等待 audio.done 信号 |
| `_handle_message(msg)` | 分发 binary→on_audio / tts.sentence_start→on_sentence_start / audio.done→on_done |

回调: `on_audio(bytes)` → TTSPipelineManager → Consumer._send_binary() 转发前端

---

## TTSPipelineManager（tts_pipeline_manager.py）

asyncio 队列管理器，编排安慰语音 → Agent 回复 → 错误播报。

| 方法 | 说明 |
|------|------|
| `start()` | 启动 worker 异步任务 + 安慰计时器 |
| `enqueue(text, item_type)` | 入队播报项（comfort/response/error） |
| `stop_comfort_timer()` | 停止安慰递进 + 清除队列中 comfort 项 |
| `wait_idle()` | 等待队列清空（所有播报完成） |
| `cancel()` | 取消全部：清空队列 + 断开 TTS + 取消 worker |
| `shutdown()` | 正常关闭：发送 sentinel + 等待 worker 结束 |

**安慰语音 3 级递进**: `VOICE_TTS_COMFORT_TEXTS` 配置 3 条安慰文本，每隔 `VOICE_TTS_COMFORT_DELAY` 秒自动入队下一级，播完自动重启计时器。Agent 完成或出错后 `stop_comfort_timer()` 停止并清除待播安慰。

**QueueItem 类型**: `comfort`（安慰） | `response`（Agent 回复） | `error`（错误播报） | `sentinel`（关闭信号）

---

## voice_messages.py — 协议消息构建

| 函数 | 返回 | 说明 |
|------|------|------|
| `error_msg(code, message, recoverable)` | `dict` | `{"type": "error", "data": {...}}` |
| `response_event(event_type, response_id, segment_id, **extra)` | `dict` | `{"type": event_type, "data": {...}}` |
| `delta_msg(content, response_id)` | `dict` | `{"type": "response.delta", "data": {...}}` |
| `build_agent_error(chunk)` | `dict` | 从 Agent 错误 StreamChunk 构建客户端错误对象 |

---

## voice_persist_service.py — 持久化服务

| 方法 | 说明 |
|------|------|
| `merge_pcm_to_wav(pcm_chunks)` | 多段 PCM base64 → 单个 WAV bytes（16kHz/16bit/mono） |
| `upload_to_minio(path, data)` | MinIO 上传音频文件 |
| `delete_from_minio(path)` | MinIO 删除音频文件 |
| `persist_audio_attachment(user_id, segment_id, request_id)` | 完整持久化流程：Redis PCM → WAV → MinIO → Message.is_voice + MediaAttachment |
| `record_only_ambient(user_id, text)` | ambient RECORD_ONLY 消息保存 + 超限 20 条自动清理 |

---

## voice_session_service.py — 会话与频率限制

| 方法 | 说明 |
|------|------|
| `create_session(user_id, mode)` | 创建 Redis 会话状态 |
| `get_session(user_id)` | 获取会话状态 JSON |
| `close_session(user_id)` | 删除会话 + 清理音频缓存 |
| `set_active_conversation(user_id)` | 设置活跃对话标记（VOICE_ACTIVE_CONV_TTL） |
| `is_active_conversation(user_id)` | 检查活跃对话标记 |
| `cache_audio_chunk(user_id, seg, pcm_b64)` | RPUSH 缓存 PCM 帧 |
| `get_audio_chunks(user_id, seg)` | LRANGE 获取 PCM 帧列表 |
| `check_ws_rate_limit(user_id)` | WS 连接频率限制（10次/分） |
| `check_llm_rate_limit(user_id)` | LLM 调用频率限制（60次/分） |

---

## 响应决策链（response_decision_service.py）

| 优先级 | 条件 | 结果 | 说明 |
|--------|------|------|------|
| 1 | 紧急停止词（停/取消/闭嘴/停止/别说了） | STOP | |
| 2 | 唤醒词精确匹配（`w in text`） | RESPOND | |
| 3 | 唤醒词模糊匹配（编辑距离<=1 或拼音相似>=0.8） | RESPOND | |
| **4** | **LLM 意图分类** | **视置信度** | **仅 ambient + VOICE_DECISION_USE_LLM=True**；httpx JSON mode，超时由 VOICE_DECISION_LLM_TIMEOUT 控制；置信度 ≥ VOICE_DECISION_LLM_THRESHOLD 采用，否则穿透；Prompt 模板: `voice_intent_classify.j2` |
| 5 | 活跃对话状态（Redis voice:active_conv 键存在） | RESPOND | |
| 6 | 多 speaker 活跃（voice:recent_speakers SCARD >= 2） | RECORD_ONLY | |
| 7 | 单 speaker + 问句特征（？/问句词/语气词） | RESPOND | |
| 8 | 默认 | RECORD_ONLY | |

**枚举**: `DecisionResult(str, Enum)` — RESPOND / RECORD_ONLY / STOP

---

## UtteranceAggregator（utterance_aggregator.py）

话语聚合器，ambient 模式专用。缓冲多段 ASR 转录，静默超时后合并触发回调。

**状态机**: `IDLE → add() → COLLECTING → timer到期/满缓冲 → AGGREGATED → 回调完成 → IDLE`

| 方法 | 说明 |
|------|------|
| `add(text)` | 追加转录文本，重置超时计时器；达到 `max_buffer_size` 则立即 flush |
| `flush()` | 立即聚合触发回调（停止词或外部强制刷新） |
| `reset()` | 清空缓冲区不触发回调（停止词命令后使用） |
| `destroy()` | 取消计时器 + 清空缓冲（会话结束调用） |

**配置**: `VOICE_AMBIENT_AGGREGATE_TIMEOUT`（静默超时 3s）、`VOICE_AMBIENT_MAX_BUFFER_SIZE`（最大缓冲 10 段）

---

## TTSRouter（tts_router.py）

跨设备 TTS 路由，通过 Django Channels `group_send` 将 TTS 音频帧广播到浏览器连接。解决 ambient 模式 ESP 设备推理 → 浏览器播放 TTS 的跨连接问题。

| 方法 | 说明 |
|------|------|
| `group_name(user_id)` | 静态方法，返回 `voice_tts_{user_id}` |
| `send_binary(user_id, data)` | group_send 发送 `tts_audio_frame` 类型音频帧 |
| `send_control(user_id, event_type, payload?)` | group_send 发送 `tts_control` 类型控制消息（tts.started/tts.completed） |
| `get_on_audio_callback(user_id)` | 返回闭包，可直接传给 TTSPipelineManager(on_audio=...) |

**Channels group handler**（consumers.py 中）:
- `tts_audio_frame(event)` → `_send_binary(event["data"])` 转发前端
- `tts_control(event)` → `_send_json(event["payload"])` 转发前端
- 设备连接（`_is_device_connection=True`）跳过音频回传


<claude-mem-context>
# Recent Activity

### Mar 7, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1588 | 12:50 AM | 🔵 | LinChat TTS Stream Client with Gateway Auto Sentence-Splitting | ~766 |
| #1582 | 12:46 AM | 🔵 | LinChat ASR Gateway WebSocket Client | ~654 |
| #1581 | " | 🔵 | LinChat TTS Comfort Queue with Gap Management | ~659 |
| #1580 | 12:45 AM | 🔵 | LinChat VoicePipeline Orchestration and Decision Logic | ~703 |
</claude-mem-context>