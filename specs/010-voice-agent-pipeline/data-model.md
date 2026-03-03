# Data Model: 语音模块迁移

**Date**: 2026-03-02 | **Status**: 无新增模型（复用现有）

## 概述

本特性不新增数据库模型。语音消息复用现有 `Message` + `MediaAttachment` 模型，语音配置复用现有 `SpeakerProfile` / `RegisteredDevice` / `VoiceSettings` 模型。

---

## 复用模型清单

### 1. Message（`apps.chat.models`，表 `message`）

**语音模式使用的字段**:

| 字段 | 类型 | 语音模式用途 |
|------|------|------------|
| `user_id` | BigInteger | 用户标识（单用户单会话，唯一隔离键） |
| `role` | CharField | user / assistant |
| `content` | TextField | 语音转录文字 (user) / AI 回复文字 (assistant) |
| `request_id` | CharField(64) | 关联 VoicePipeline 请求 ID |
| `is_voice` | Boolean | `True` — 标记为语音消息 |
| `speaker_id` | CharField(100) | 声纹识别的说话人 ID（可选） |
| `status` | SmallInteger | 0=失败, 1=正常, 2=生成中, 3=中断 |
| `sequence` | Integer | 消息序号 |
| `created_time` | DateTime | 创建时间 |

**关联**:
- `attachments` → `MediaAttachment`（ForeignKey，音频附件）

**本特性变更**: 无字段变更。VoicePipeline 在 `AgentService.execute()` 创建 Message 后补充设置 `is_voice=True` + 关联音频附件。

---

### 2. MediaAttachment（`apps.media.models`，表 `media_attachment`）

**语音模式使用的字段**:

| 字段 | 类型 | 语音模式用途 |
|------|------|------------|
| `message` | FK → Message | 关联用户语音消息 |
| `user_id` | BigInteger | 上传用户 |
| `media_type` | CharField | `"audio"` |
| `mime_type` | CharField | `"audio/wav"` |
| `file_name` | CharField | `"voice_{segment_id}.wav"` |
| `file_size` | BigInteger | WAV 文件字节数 |
| `storage_path` | CharField | MinIO 路径: `media/{user_id}/{date}/{uuid}.wav` |
| `duration_seconds` | Float | 语音时长 |
| `expires_at` | DateTime | 过期时间（复用 MEDIA_EXPIRY_DAYS 配置） |

**本特性变更**: 无字段变更。VoicePipeline 将 PCM 缓存合并为 WAV 后上传到 MinIO 并创建 MediaAttachment 记录。

---

### 3. SpeakerProfile（`apps.voice.models`，表 `voice_speaker_profile`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user` | OneToOne → SysUser | 用户关联 |
| `gateway_speaker_id` | CharField(100, unique) | Gateway 声纹 ID |
| `name` | CharField(50) | 声纹名称 |
| `quality_score` | Float(null) | 注册质量分 |
| `enrolled_at` | DateTime | 注册时间 |

**本特性变更**: 无。声纹功能保持现有 REST API 不变。

---

### 4. RegisteredDevice（`apps.voice.models`，表 `voice_registered_device`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `device_uuid` | CharField(36, unique) | 设备 UUID |
| `user` | FK → SysUser | 所属用户 |
| `name` | CharField(100) | 设备名称 |
| `api_token_encrypted` | CharField(512) | SM4 加密 Token |
| `token_prefix` | CharField(8) | 快速查找前缀 |
| `is_active` | Boolean | 是否启用 |

**本特性变更**: 无。

---

### 5. VoiceSettings（`apps.voice.models`，表 `voice_settings`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user` | OneToOne → SysUser | 用户关联 |
| `wake_words` | JSONField | 唤醒词列表 |
| `recording_mode` | CharField | hold / toggle |
| `vad_sensitivity` | Float(0.0~1.0) | VAD 灵敏度 |

**本特性变更**: 无。`vad_sensitivity` 不再传递给 Gateway（Gateway 内置 VAD 使用自己的阈值），但保留字段供前端 UI 显示。

---

### 6. LangGraphExecution（`apps.chat.models`，表 `langgraph_execution`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_uuid` | CharField(36) | 执行唯一 ID |
| `request_id` | CharField(64) | 关联请求 |
| `user_id` | BigInteger | 用户 |
| `thread_id` | CharField(64) | LangGraph 线程 |
| `graph_name` | CharField(100) | Agent 名称 |
| `status` | CharField(20) | pending/running/completed/failed |
| `start_time` / `end_time` / `duration_ms` | 时间信息 | |
| `total_prompt_tokens` / `total_completion_tokens` | Token 统计 | |
| `langfuse_trace_id` / `langfuse_url` | Langfuse 追踪 | |

**本特性变更**: 无。`AgentService.execute()` 自动创建执行记录。

---

## Redis 状态（非持久化）

| 键模式 | TTL | 用途 | 变更 |
|--------|-----|------|------|
| `voice:session:{uid}` | 120s | 会话状态 JSON | 移除 `gateway_session_id`，新增 `asr_session_id` |
| `voice:audio_chunks:{uid}:{seg}` | 300s | PCM 音频帧缓存 | 不变 |
| `voice:llm_rate:{uid}` | 60s | LLM 频率限制 | 不变 |
| `voice:active_conv:{uid}` | 30s | 活跃对话标记 | 不变 |

---

## ER 关系图

```
SysUser (1) ──── (1) SpeakerProfile
    │
    ├── (1) ──── (1) VoiceSettings
    │
    ├── (1) ──── (*) RegisteredDevice
    │
    ├── (1) ──── (*) Message
    │                 │
    │                 └── (1) ──── (*) MediaAttachment [audio/wav]
    │
    └── (1) ──── (*) LangGraphExecution
```

**注意**: 所有关联通过 `user_id` 串联，无 `conversation_id`。
