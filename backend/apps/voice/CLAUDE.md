# Voice 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 语音交互模块：WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹注册/识别、设备管理、响应决策。

---

## 文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `consumers.py` | 142 | VoiceConsumer 骨架（Mixin 组装 + connect/disconnect/receive + 设备 Token 认证 + WS 连接频率限制 + TTS Channels 分组管理 + `tts_audio_frame`/`tts_control` group handler） |
| `consumer_events.py` | 88 | EventMixin — ASR 事件分发：vad.speech_start/end、transcription.completed/failed、error → 前端协议翻译；**ambient 分支**: `_handle_ambient_transcription()` 停止词预检 + 聚合器路由 + `aggregation.utterance_added` 事件 |
| `consumer_inference.py` | 68 | InferenceMixin — VoicePipeline 后台启动（voice_chat/ambient）、空闲超时循环（**ambient 模式跳过**）；精简 docstring 和格式 |
| `consumer_session.py` | 135 | SessionMixin — ASRStreamClient 连接/配置/断开、cancel、音频帧转发、语音段超时定时器；**ambient**: UtteranceAggregator 初始化 + `_on_utterance_aggregated()` 聚合回调 + `_reconnect_asr()` ASR 自动重连；新增 `_handle_asr_failure()` 统一 ASR 失败处理 |
| `models.py` | 76 | SpeakerProfile / RegisteredDevice / VoiceSettings |
| `repositories.py` | 104 | 3 个 Repo（SpeakerProfile/RegisteredDevice/VoiceSettings） |
| `serializers.py` | 74 | 6 个序列化器（SpeakerProfile/Device/Settings/SettingsUpdate/CreateDevice/CreateSpeaker） |
| `views.py` | 118 | REST 视图：声纹注册/删除、设备注册/删除/列表、语音设置 CRUD |
| `urls.py` | 28 | REST 路由（speakers/、devices/、settings/） |
| `routing.py` | 12 | WebSocket 路由（`ws/voice/`） |

---

## Consumer Mixin 架构

```
VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer)
```

- **SessionMixin** (`consumer_session.py`): ASRStreamClient 连接/配置/断开、response.cancel → VoicePipeline.cancel()、音频帧转发 + PCM 缓存、语音段超时保护（VOICE_MAX_SEGMENT_DURATION → ASR commit）；**ambient 模式**: UtteranceAggregator 初始化 + `_on_utterance_aggregated()` 聚合回调（aggregation.completed → decision.result → RESPOND/RECORD_ONLY 路由）+ `_reconnect_asr()` ASR 断连自动重连（最多 3 次，间隔 2s）
- **EventMixin** (`consumer_events.py`): Gateway ASR 事件 → 前端协议翻译（vad.speech_start/end、transcription.complete/failed、error），transcription.completed 触发：voice_chat → InferenceMixin._start_voice_pipeline()；**ambient → `_handle_ambient_transcription()`（停止词预检 + aggregator.add + aggregation.utterance_added 事件）**
- **InferenceMixin** (`consumer_inference.py`): VoicePipeline.run_pipeline() 后台 asyncio.Task 启动，15 秒周期空闲超时检查（VOICE_IDLE_TIMEOUT）；**ambient 模式直接跳过空闲超时**

---

## 语音模式

| 模式 | 说明 | 决策 |
|------|------|------|
| `voice_chat` | 标准语音对话 | 直接进入 Agent + TTS |
| `ambient` | 环境监听（014-jarvis） | UtteranceAggregator 聚合 → ResponseDecisionService 决策 → TTSRouter 路由 TTS |

> `voice_chat_enriched` 已废弃，前端发送时静默映射为 `voice_chat` (SC-008)

---

## 核心模型

| 模型 | 表名 | 说明 |
|------|------|------|
| SpeakerProfile | `voice_speaker_profile` | OneToOne->SysUser，gateway_speaker_id，quality_score |
| RegisteredDevice | `voice_registered_device` | SM4 加密 Token（api_token_encrypted），token_prefix 快速查找，is_active |
| VoiceSettings | `voice_settings` | wake_words（JSON 数组）、recording_mode（hold/toggle）、vad_sensitivity（0.0~1.0） |

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/voice/speakers/` | 查询当前用户声纹 |
| POST | `/api/v1/voice/speakers/` | 注册声纹（MultiPart: name + audio WAV） |
| DELETE | `/api/v1/voice/speakers/delete/` | 删除声纹 |
| GET | `/api/v1/voice/devices/` | 设备列表 |
| POST | `/api/v1/voice/devices/` | 注册设备（返回明文 Token） |
| DELETE | `/api/v1/voice/devices/<uuid>/` | 停用设备 |
| GET | `/api/v1/voice/settings/` | 获取语音设置 |
| PUT | `/api/v1/voice/settings/` | 更新语音设置 |

---

## Redis 键

| 键模式 | TTL | 用途 |
|--------|-----|------|
| `voice:session:{uid}` | VOICE_SESSION_TTL（ambient: VOICE_AMBIENT_SESSION_TTL=3600s） | 会话状态 JSON（state, started_at, upstream_connected, asr_session_id, mode） |
| `voice:active_conv:{uid}` | VOICE_ACTIVE_CONV_TTL | 活跃对话标记（ambient 模式自动 RESPOND） |
| `voice:audio_chunks:{uid}:{seg}` | VOICE_AUDIO_CACHE_TTL | PCM 帧列表（base64 编码，RPUSH） |
| `voice:llm_rate:{uid}` | 60s | LLM 频率限制（每分钟 60 次） |
| `voice:recent_speakers:{uid}` | 60s | 说话人集合（SCARD >= 2 → RECORD_ONLY） |
| `voice:ws_connect_rate:{uid}` | 60s | WS 连接频率限制（每分钟 10 次） |

---

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.chat` | Message 模型（语音消息复用，is_voice 标记） |
| `apps.media` | MediaAttachment（音频附件 WAV 持久化） |
| `apps.graph` | AgentService（LangGraph 推理）、InferenceService（任务注册/取消） |
| `apps.common.storage` | MinIO 音频文件上传/删除 |
| `apps.common.async_utils` | `cancel_task()` / `cancel_task_sync()` 异步任务取消工具 |
| `apps.users` | SysUser + SM4 加密（设备 Token） |
| `apps.models` | model_service.get_active_model("tool")（LLM 意图分类获取模型配置） |
| Django Channels | WebSocket（Redis DB3）+ group_send（TTSRouter 跨设备 TTS 广播） |
| websockets | Gateway ASR/TTS WS 客户端（通过 `BaseWSClient` 基类） |
| httpx | LLM 意图分类 HTTP 请求（ResponseDecisionService） |
| pypinyin | 唤醒词模糊匹配（拼音相似度） |

---

## 语音管道架构

```
ESP 设备/浏览器 (PCM 音频) → WebSocket → VoiceConsumer (3 Mixin 架构)
  → ASRStreamClient(BaseWSClient) → Gateway ASR (长期存活, 心跳 30s/60s)
  → [voice_chat] transcription → VoicePipeline → Agent → TTSPipelineManager → 前端播放
  → [ambient]    transcription → UtteranceAggregator (3s 聚合) → ResponseDecisionService
                                  → RESPOND: VoicePipeline → TTSRouter (group_send) → 浏览器播放
                                  → RECORD_ONLY: voice_persist_service.record_only_ambient()（保存消息 + 上限 20 条自动清理）
                                  → STOP: 取消管道 + 重置聚合器
```

---

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/voice/ -v                  # 全部语音测试（18 个文件）
pytest tests/voice/test_consumers.py -v # Consumer 生命周期
pytest tests/voice/test_voice_pipeline.py -v  # Pipeline 编排
pytest tests/voice/test_response_decision.py -v  # 决策链
```

### 测试文件映射

| 测试文件 | 覆盖模块 |
|----------|---------|
| `test_consumers.py` | consumers.py + 3 Mixin（consumer_events/session/inference） |
| `test_voice_pipeline.py` | voice_pipeline.py（管道编排、barge-in、TTS 管理） |
| `test_response_decision.py` | response_decision_service.py（8 级决策链、模糊匹配） |
| `test_response_decision_llm.py` | response_decision_service.py（LLM 意图分类、置信度阈值） |
| `test_utterance_aggregator.py` | utterance_aggregator.py（缓冲、超时、满缓冲） |
| `test_tts_pipeline_manager.py` | tts_pipeline_manager.py（安慰语音、队列、cancel） |
| `test_tts_router.py` | tts_router.py（group_send、跨设备路由） |
| `test_tts_stream_client.py` | tts_stream_client.py（WS 连接、配置、文本增量） |
| `test_asr_stream_client.py` | asr_stream_client.py（WS 连接、事件接收） |
| `test_voice_session.py` | voice_session_service.py（Redis 会话、音频缓存、频率限制） |
| `test_device_service.py` | device_service.py（设备注册、Token SM4 加密） |
| `test_speaker_service.py` | speaker_service.py（声纹注册/删除、Gateway 对接） |
| `test_models.py` | models.py（ORM 模型、关联关系） |
| `test_repositories.py` | repositories.py（Repository CRUD） |
| `test_views.py` | views.py（REST API、权限、验证） |
| `test_latency_benchmark.py` | 性能基准（延迟测试） |


<claude-mem-context>
# Recent Activity

### Mar 7, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1590 | 12:51 AM | 🔵 | LinChat Voice Consumer Session Management | ~719 |
| #1579 | 12:45 AM | 🔵 | LinChat Existing Voice Consumer Architecture | ~608 |
</claude-mem-context>