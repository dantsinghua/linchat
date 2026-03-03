# Voice 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 语音交互模块：WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹注册/识别、设备管理、响应决策。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `consumers.py` | VoiceConsumer 骨架（Mixin 组装 + connect/disconnect/receive + 设备 Token 认证 + WS 连接频率限制） |
| `consumer_events.py` | EventMixin — ASR 事件分发：vad.speech_start/end、transcription.completed/failed、error → 前端协议翻译 |
| `consumer_inference.py` | InferenceMixin — VoicePipeline 后台启动（voice_chat/continuous_listen）、空闲超时循环 |
| `consumer_session.py` | SessionMixin — ASRStreamClient 连接/配置/断开、cancel（VoicePipeline.cancel）、音频帧转发、语音段超时定时器 |
| `models.py` | SpeakerProfile / RegisteredDevice / VoiceSettings |
| `repositories.py` | 3 个 Repo（SpeakerProfile/RegisteredDevice/VoiceSettings） |
| `serializers.py` | 6 个序列化器（SpeakerProfile/Device/Settings/SettingsUpdate/CreateDevice/CreateSpeaker） |
| `views.py` | REST 视图：声纹注册/删除、设备注册/删除/列表、语音设置 CRUD |
| `urls.py` | REST 路由（speakers/、devices/、settings/） |
| `routing.py` | WebSocket 路由（`ws/voice/`） |

---

## Consumer Mixin 架构

```
VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer)
```

- **SessionMixin** (`consumer_session.py`): ASRStreamClient 连接/配置/断开、response.cancel → VoicePipeline.cancel()、音频帧转发 + PCM 缓存、语音段超时保护（VOICE_MAX_SEGMENT_DURATION → ASR commit）
- **EventMixin** (`consumer_events.py`): Gateway ASR 事件 → 前端协议翻译（vad.speech_start/end、transcription.complete/failed、error），transcription.completed 触发 InferenceMixin._start_voice_pipeline()
- **InferenceMixin** (`consumer_inference.py`): VoicePipeline.run_pipeline() 后台 asyncio.Task 启动，15 秒周期空闲超时检查（VOICE_IDLE_TIMEOUT）

---

## 语音模式

| 模式 | 说明 | 决策 |
|------|------|------|
| `voice_chat` | 标准语音对话 | 直接进入 Agent + TTS |
| `continuous_listen` | 持续监听 | ResponseDecisionService 决策（RESPOND/RECORD_ONLY/STOP） |

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
| `voice:session:{uid}` | VOICE_SESSION_TTL | 会话状态 JSON（state, started_at, upstream_connected, asr_session_id） |
| `voice:active_conv:{uid}` | VOICE_ACTIVE_CONV_TTL | 活跃对话标记（continuous_listen 模式自动 RESPOND） |
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
| `apps.users` | SysUser + SM4 加密（设备 Token） |
| Django Channels | WebSocket（Redis DB3） |
| websockets | Gateway ASR WS 客户端 + TTS 流式 WS 客户端 |
| pypinyin | 唤醒词模糊匹配（拼音相似度） |

---

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/voice/ -v
```
