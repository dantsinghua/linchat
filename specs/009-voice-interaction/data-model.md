# Data Model: 语音交互

**Feature Branch**: `009-voice-interaction`
**Date**: 2026-02-14

## 1. Entity Relationship Diagram

```
┌────────────────┐     ┌────────────────────┐     ┌─────────────────┐
│    SysUser      │────│   SpeakerProfile    │     │ RegisteredDevice│
│  (apps/users)   │  1:1│  (apps/voice)       │     │  (apps/voice)   │
│                 │     │                    │     │                 │
│ user_id (PK)    │     │ user (FK→SysUser)  │     │ device_id (PK)  │
│ username        │     │ gateway_speaker_id │     │ user (FK)       │
│ status          │     │ name              │     │ name            │
│ ...             │     │ enrolled_at       │     │ api_token_enc   │
└────────┬───────┘     └────────────────────┘     │ is_active       │
         │                                         └─────────────────┘
         │ 1:1
         │
┌────────▼───────┐     ┌────────────────────┐
│ VoiceSettings   │     │     Message         │
│  (apps/voice)   │     │   (apps/chat)       │
│                 │     │                    │
│ user (FK)       │     │ message_id (PK)    │
│ wake_words      │     │ content (STT 转写)  │
│ recording_mode  │     │ is_voice (NEW)     │
│ vad_sensitivity │     │ speaker_id (NEW)   │◄── llmgateway speaker_id
└─────────────────┘     │ ...                │
                        │ attachments ────────│──► MediaAttachment
                        └────────────────────┘         │
                                                       │ media_type='audio'
                                                       │ storage_path → MinIO
                                                       │ duration_seconds
                                                       └──────────────────

Redis (瞬态)                         MinIO (持久)
┌──────────────────────┐            ┌──────────────────────┐
│ voice:session:{uid}   │            │ media/{uid}/{date}/   │
│ voice:active_conv:{uid}│           │   {uuid}.wav          │
│ channels:*            │            │   (音频文件)           │
└──────────────────────┘            └──────────────────────┘
```

## 2. Entity Definitions

### 2.1 Message 模型扩展（apps/chat/models.py）

**表名**: `chat_message`（现有表，新增字段）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `is_voice` | BooleanField | default=False, db_index=True | 语音消息标记 |
| `speaker_id` | CharField(100) | null=True, blank=True | llmgateway 声纹识别返回的 speaker_id |

**迁移**: `0005_message_voice_fields.py`（0004 已被 `0004_remove_thumbnail_add_document_type.py` 占用）
- 两个字段均为 nullable/有默认值，不锁表
- 对 `is_voice` 建索引用于过滤查询

**音频存储**: 通过现有 MediaAttachment 关联
- `media_type='audio'` 标识音频附件
- `storage_path` → MinIO 路径
- `duration_seconds` → 音频时长（已有字段）
- 查询: `message.attachments.filter(media_type='audio').first()`

### 2.2 SpeakerProfile 声纹匹配表（NEW: apps/voice/models.py）

**表名**: `voice_speaker_profile`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BigAutoField | PK | 主键 |
| `user` | OneToOneField(SysUser) | unique, on_delete=CASCADE | 关联 LinChat 用户 |
| `gateway_speaker_id` | CharField(100) | unique | llmgateway 声纹用户 ID |
| `name` | CharField(50) | | 显示名称（如 "爸爸"） |
| `quality_score` | FloatField | null=True | llmgateway 返回的声纹质量评分（0.0-1.0） |
| `enrolled_at` | DateTimeField | auto_now_add=True | 注册时间 |
| `created_at` | DateTimeField | auto_now_add=True | 创建时间 |
| `updated_at` | DateTimeField | auto_now=True | 更新时间 |

**索引**:
- `user` (unique, OneToOne 自带)
- `gateway_speaker_id` (unique)

**查询模式**:
- 按 `gateway_speaker_id` 查找 → 声纹匹配后找 LinChat 用户
- 按 `user_id` 查找 → 用户查看/删除自己的声纹

### 2.3 RegisteredDevice 注册设备（NEW: apps/voice/models.py）

**表名**: `voice_registered_device`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BigAutoField | PK | 主键 |
| `device_uuid` | CharField(36) | unique | 设备公开标识 |
| `user` | ForeignKey(SysUser) | on_delete=CASCADE | 设备注册者 |
| `name` | CharField(100) | | 设备名称（如 "客厅树莓派"） |
| `api_token_encrypted` | CharField(512) | | SM4 加密的 API Token |
| `token_prefix` | CharField(8) | db_index=True | Token 前 8 位（快速查找） |
| `is_active` | BooleanField | default=True | 是否启用 |
| `created_at` | DateTimeField | auto_now_add=True | 注册时间 |
| `last_active_at` | DateTimeField | null=True | 最后活跃时间 |

**索引**:
- `device_uuid` (unique)
- `token_prefix` (用于 Token 认证快速查找)
- `user_id` + `is_active` (用户查看活跃设备)

**Token 认证流程**:
1. 设备发送 Token → 取前 8 位 → 查 `token_prefix` 匹配的设备
2. SM4 解密 `api_token_encrypted` → 全量比对
3. 认证通过 → 更新 `last_active_at`

### 2.4 VoiceSettings 语音设置（NEW: apps/voice/models.py）

**表名**: `voice_settings`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BigAutoField | PK | 主键 |
| `user` | OneToOneField(SysUser) | unique, on_delete=CASCADE | 关联用户 |
| `wake_words` | JSONField | default=["小鱼"] | 唤醒词列表 |
| `recording_mode` | CharField(10) | default='toggle', choices=['hold','toggle'] | 录音模式 |
| `vad_sensitivity` | FloatField | default=0.5, validators=[0.0, 1.0] | VAD 灵敏度 |
| `created_at` | DateTimeField | auto_now_add=True | 创建时间 |
| `updated_at` | DateTimeField | auto_now=True | 更新时间 |

**默认值策略**: 首次访问语音设置时自动创建（`get_or_create`）

### 2.5 系统默认用户（数据迁移）

**迁移**: `0002_create_unknown_user.py`

```python
# 在 SysUser 表创建全局单例用户
SysUser.objects.get_or_create(
    username="unknown",
    defaults={
        "status": 0,        # 禁用（is_active() 返回 False，不可登录）
        "type": "user",
        "password_hash": "",  # 无密码
    }
)
```

## 3. Redis Key 设计

| Key 模式 | 类型 | TTL | 说明 |
|----------|------|-----|------|
| `voice:session:{user_id}` | String (JSON) | 120s | 语音会话状态 `{state, started_at, upstream_connected}` |
| `voice:active_conv:{user_id}` | String | 30s | 活跃对话标记（存在即活跃） |
| `voice:audio_chunks:{user_id}:{segment_id}` | List | 300s | 音频帧缓存（用于保存到 MinIO） |
| `voice:recent_speakers:{user_id}` | Set | 60s | 最近活跃的不同 speaker_id 集合（用于 FR-021 多因素响应决策：≥2 个不同 speaker 活跃时降低自动回复倾向） |
| `voice:stt_pending:{user_id}:{segment_id}` | String | 60s | 异步 STT 转写任务状态（pending/completed/failed），用于跟踪 LinChat 自行发起的 HTTP 转写请求 |
| `voice:stt_result:{user_id}:{segment_id}` | String | 120s | STT 转写文本缓存（协调 STT 与消息持久化时序） |
| `voice:llm_rate:{user_id}` | String (counter) | 60s | LLM 推理频率限制计数器（宪法 4.1：大模型 60 次/分/用户），每次触发推理时 INCR |

**Channels Layer Key**（由 channels-redis 自动管理，使用 Redis DB3，独立于 DB0 缓存/DB1 Langfuse/DB2 Celery Broker）:
- `asgi:group:voice_{user_id}` → WebSocket group 消息分发

## 4. MinIO 存储结构

**Bucket**: `linchat-media`（复用现有 bucket）

**语音文件路径**: `media/{user_id}/{YYYY-MM-DD}/{uuid}.wav`
- 复用 MediaAttachment 的存储路径规范
- 文件格式: 后端接收 PCM16 原始帧后添加 44-byte WAV 头存储为 `.wav` 文件（PCM16 16kHz mono，与原始采集格式一致，无需编解码转换）
- 过期清理: 复用现有 `clean_expired_media` Celery 任务（默认 7 天）

## 5. 配置参数

| 参数 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| `LLM_GATEWAY_WS_URL` | `ws://127.0.0.1:8888` | ✅ | llmgateway WebSocket 端点（⚠️ 不可使用 8081，已被 Langfuse Nginx 占用） |
| `LLM_GATEWAY_HTTP_URL` | `http://127.0.0.1:8889` | ✅ | llmgateway HTTP REST 端点（声纹注册/管理） |
| `LLM_GATEWAY_WS_API_KEY` | (same as LLM_GATEWAY_API_KEY) | ✅ | WebSocket 认证密钥（HTTP 端点复用同一密钥，通过 `Authorization: Bearer` 头传递） |
| `VOICE_SESSION_TTL` | 120 | | 语音会话 Redis TTL（秒） |
| `VOICE_ACTIVE_CONV_TTL` | 30 | | 活跃对话超时（秒） |
| `VOICE_MAX_RECORDING_SECONDS` | 30 | | 单次录音最大时长 |
| `VOICE_DEFAULT_WAKE_WORDS` | ["小鱼"] | | 默认唤醒词 |
| `VOICE_SPEAKER_THRESHOLD` | 0.6 | | 声纹匹配置信度阈值 |
| `VOICE_VAD_THRESHOLD` | 0.5 | | 默认 VAD 灵敏度 |
| `VOICE_AUDIO_CACHE_TTL` | 300 | | 音频帧 Redis 缓存 TTL（秒），用于断线重连恢复 |
