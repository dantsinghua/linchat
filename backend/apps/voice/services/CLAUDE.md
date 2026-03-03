# Voice Services 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> `apps/voice/services/` 语音交互业务逻辑层。

---

## 文件清单

| 文件 | 职责 | 全局实例 |
|------|------|---------|
| `asr_stream_client.py` | Gateway ASR WebSocket 流式客户端（连接/配置/音频转发/事件接收） | 无（每个 Consumer 创建） |
| `tts_stream_client.py` | Gateway TTS 流式 WebSocket 客户端（文本输入/PCM 音频输出） | 无（每次 pipeline 创建） |
| `voice_pipeline.py` | 语音推理管道编排（ASR→Agent→TTS + 持久化 + 持续监听） | 无（静态方法） |
| `voice_session_service.py` | 语音会话生命周期 + Redis 状态 + 音频缓存 + 频率限制 | `voice_session_service` |
| `voice_persist_service.py` | PCM→WAV 转换 + MinIO 上传/删除 | `voice_persist_service` |
| `speaker_service.py` | 声纹注册/删除/识别（对接 Gateway HTTP） | `speaker_service` |
| `device_service.py` | 设备注册/Token 管理（SM4 加密） | `device_service` |
| `response_decision_service.py` | 唤醒词检测 + 响应决策（RESPOND/RECORD_ONLY/STOP） | `response_decision_service` |
| `voice_settings_service.py` | 语音设置 CRUD（get_or_create + update） | `voice_settings_service` |

---

## 服务依赖关系

```
VoiceConsumer
  ├── ASRStreamClient        — Gateway ASR WebSocket 通信
  ├── voice_session_service  — 会话状态 / 音频缓存 / 频率限制
  ├── VoicePipeline          — Agent + TTS 编排 + 持久化
  │     ├── AgentService（apps.graph）
  │     ├── TTSStreamClient
  │     ├── voice_persist_service — PCM→WAV + MinIO
  │     └── message_repo（apps.chat）
  ├── speaker_service        — 声纹识别
  └── response_decision_service — 唤醒词 + 响应决策（continuous_listen 模式）
```

---

## VoicePipeline 编排流程

```
ASR transcription.completed
  → run_pipeline(mode=voice_chat|continuous_listen)
    → [continuous_listen] ResponseDecisionService.decide()
      → RESPOND: 完整 pipeline
      → RECORD_ONLY: 仅保存 user Message + 音频附件
      → STOP: cancel() 取消
    → [voice_chat] 直接进入
      → rate limit check → InferenceService.register_task()
      → TTSStreamClient.connect() → AgentService.execute() 流式
      → response.delta + TTS text.delta → TTS flush → response.end
      → persist_audio_attachment()
```

---

## 响应决策链（response_decision_service.py）

| 优先级 | 条件 | 结果 |
|--------|------|------|
| 1 | 紧急停止词（停/取消/闭嘴/停止/别说了） | STOP |
| 2 | 唤醒词精确匹配 | RESPOND |
| 3 | 唤醒词模糊匹配（编辑距离<=1 或拼音相似>=0.8） | RESPOND |
| 4 | 活跃对话状态（Redis 键存在） | RESPOND |
| 5 | 多 speaker 活跃（recent_speakers >= 2） | RECORD_ONLY |
| 6 | 单 speaker + 问句特征 | RESPOND |
| 7 | 默认 | RECORD_ONLY |


<claude-mem-context>

</claude-mem-context>