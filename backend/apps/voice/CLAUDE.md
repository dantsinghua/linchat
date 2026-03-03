# Voice 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 语音交互模块：WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹注册/识别、设备管理、响应决策。

## 文件清单

| 文件 | 职责 |
|------|------|
| `consumers.py` | VoiceConsumer 骨架（Mixin 组装 + connect/disconnect/receive） |
| `consumer_events.py` | EventMixin — ASR 事件翻译：VAD/转录/错误 → 前端协议 |
| `consumer_inference.py` | InferenceMixin — VoicePipeline 启动、空闲超时 |
| `consumer_session.py` | SessionMixin — ASRStreamClient 会话管理、音频帧转发 |
| `models.py` | SpeakerProfile / RegisteredDevice / VoiceSettings |
| `repositories.py` | 3 个 Repo（Speaker/Device/VoiceSettings） |
| `serializers.py` | 声纹/设备/设置序列化器 |
| `views.py` | REST 视图：声纹/设备/设置 CRUD |
| `urls.py` | REST 路由 |
| `routing.py` | WebSocket 路由（`ws/voice/`） |

## Consumer Mixin 架构

```
VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer)
```

- **SessionMixin** (`consumer_session.py`): ASRStreamClient 连接/配置/断开、cancel、音频帧转发
- **EventMixin** (`consumer_events.py`): Gateway ASR 事件 → 前端协议翻译（VAD/转录/错误）
- **InferenceMixin** (`consumer_inference.py`): VoicePipeline 启动（voice_chat / continuous_listen）、空闲超时

## 语音模式

| 模式 | 说明 | 决策 |
|------|------|------|
| `voice_chat` | 标准语音对话 | 直接进入 Agent + TTS |
| `continuous_listen` | 持续监听 | ResponseDecisionService 决策（RESPOND/RECORD_ONLY/STOP） |

> `voice_chat_enriched` 已废弃，前端发送时静默映射为 `voice_chat` (SC-008)

## 核心模型

| 模型 | 表名 | 说明 |
|------|------|------|
| SpeakerProfile | `voice_speaker_profile` | OneToOne->SysUser，gateway_speaker_id |
| RegisteredDevice | `voice_registered_device` | SM4 加密 Token，token_prefix 快速查找 |
| VoiceSettings | `voice_settings` | 唤醒词、录音模式、VAD 灵敏度 |

## Redis 键

| 键模式 | TTL | 用途 |
|--------|-----|------|
| `voice:session:{uid}` | 120s | 会话状态（含 asr_session_id） |
| `voice:active_conv:{uid}` | 30s | 活跃对话标记 |
| `voice:audio_chunks:{uid}:{seg}` | 300s | PCM 缓冲 |
| `voice:llm_rate:{uid}` | 60s | LLM 频率限制 |
| `voice:recent_speakers:{uid}` | 60s | 说话人集合 |
| `voice:ws_connect_rate:{uid}` | 60s | WS 连接限制 |

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.chat` | Message 模型（语音消息复用） |
| `apps.media` | MediaAttachment（音频附件） |
| `apps.graph` | AgentService（LangGraph 推理）、InferenceService（任务管理） |
| `apps.users` | SysUser + SM4 加密 |
| Django Channels | WebSocket（Redis DB3） |
| websockets | Gateway ASR WS + TTS WS |
| pypinyin | 唤醒词模糊匹配 |

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/voice/ -v
```
