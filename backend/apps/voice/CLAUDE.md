# Voice 模块开发指南

> 本文件为 `apps/voice` 语音交互模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

语音交互模块，负责：WebSocket 语音流式代理、声纹注册与识别、外部设备注册与 Token 管理、语音设置管理、响应决策（唤醒词检测 + 应答策略）。

**不负责**：消息存储（在 `apps/chat`）、用户认证流程（在 `apps/users`）、Agent 执行（在 `apps/graph/`）、模型配置（在 `apps/models/`）。

---

## 目录结构

```
apps/voice/
├── consumers.py       # WebSocket Consumer（语音流式交互入口）
├── routing.py         # WebSocket 路由配置
├── models.py          # 数据模型（SpeakerProfile, RegisteredDevice, VoiceSettings）
├── views.py           # REST 视图（声纹、设备、设置 CRUD）
├── urls.py            # REST 路由配置
├── serializers.py     # DRF 序列化器（请求验证 + 响应格式化）
├── repositories.py    # 数据访问层（Speaker/Device/VoiceSettings Repo）
├── services/          # 业务逻辑服务包（详见下方说明）
├── apps.py            # Django App 配置
└── migrations/        # 数据库迁移
```

---

## services/ 目录说明

| 文件 | 职责 |
|------|------|
| `gateway_client.py` | llmgateway WebSocket 客户端管理，负责与 Gateway 的 WebSocket 长连接建立、心跳、重连 |
| `voice_session_service.py` | 语音会话生命周期管理 + Redis 状态管理 + STT 转写结果处理 |
| `speaker_service.py` | 声纹注册/删除/识别，对接 llmgateway HTTP 接口进行声纹特征提取与比对 |
| `device_service.py` | 外部设备注册/Token 管理，使用 SM4 加密存储设备 API Token |
| `response_decision_service.py` | 唤醒词检测 + 响应决策逻辑，输出决策结果：`RESPOND`（回复）/ `RECORD_ONLY`（仅记录）/ `STOP`（停止） |
| `voice_settings_service.py` | 语音设置获取与更新，封装 VoiceSettings Repository 调用（get_or_create + update） |

---

## 核心数据模型

### SpeakerProfile（声纹档案）

存储用户注册的声纹信息，包含说话人名称、声纹特征 ID（Gateway 返回）、注册时间等。按 `user_id` 隔离。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `user` | OneToOneField → SysUser | CASCADE, related_name="speaker_profile" | 关联用户 |
| `gateway_speaker_id` | CharField(100) | unique | llmgateway 声纹用户 ID |
| `name` | CharField(50) | - | 显示名称 |
| `quality_score` | FloatField | null=True, 0.0~1.0 | 声纹质量评分 |
| `enrolled_at` | DateTimeField | auto_now_add | 声纹注册时间 |
| `created_at` | DateTimeField | auto_now_add | 创建时间 |
| `updated_at` | DateTimeField | auto_now | 更新时间 |

**表名**: `voice_speaker_profile`

### RegisteredDevice（注册设备）

存储外部设备信息（如智能音箱），包含设备名称、API Token（SM4 加密存储）、激活状态等。按 `user_id` 隔离。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `device_uuid` | CharField(36) | unique | 设备公开标识 |
| `user` | ForeignKey → SysUser | CASCADE, related_name="registered_devices" | 设备注册者 |
| `name` | CharField(100) | - | 设备名称 |
| `api_token_encrypted` | CharField(512) | - | SM4 加密的 API Token |
| `token_prefix` | CharField(8) | db_index | Token 前 8 位（快速查找） |
| `is_active` | BooleanField | default=True | 是否启用 |
| `created_at` | DateTimeField | auto_now_add | 注册时间 |
| `last_active_at` | DateTimeField | null=True | 最后活跃时间 |

**表名**: `voice_registered_device`
**索引**: `idx_device_user_active`(`user_id`, `is_active`)

### VoiceSettings（语音设置）

存储用户的语音交互偏好配置，每个用户一条记录（OneToOne）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `user` | OneToOneField → SysUser | CASCADE, related_name="voice_settings" | 关联用户 |
| `wake_words` | JSONField | default=list | 唤醒词列表 |
| `recording_mode` | CharField(10) | choices: `hold`(按住说话) / `toggle`(点击切换), default=`toggle` | 录音模式 |
| `vad_sensitivity` | FloatField | 0.0~1.0, default=0.5 | VAD 灵敏度 |
| `created_at` | DateTimeField | auto_now_add | 创建时间 |
| `updated_at` | DateTimeField | auto_now | 更新时间 |

**表名**: `voice_settings`

---

## API 端点

### WebSocket

| 协议 | 路径 | 说明 |
|------|------|------|
| WebSocket | `ws/voice/` | 语音流式交互（音频上传、STT 转写、TTS 播放、会话控制） |

### REST

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/voice/speakers/` | 查询当前用户的声纹信息 |
| POST | `/api/v1/voice/speakers/` | 注册新声纹（multipart/form-data 接收音频） |
| DELETE | `/api/v1/voice/speakers/delete/` | 删除当前用户的声纹 |
| GET | `/api/v1/voice/devices/` | 设备列表查询 |
| POST | `/api/v1/voice/devices/` | 注册新设备 |
| DELETE | `/api/v1/voice/devices/{device_uuid}/` | 停用设备（软删除，设置 is_active=False） |
| GET | `/api/v1/voice/settings/` | 获取语音设置（不存在则自动创建默认值） |
| PUT | `/api/v1/voice/settings/` | 更新语音设置（支持部分更新） |

---

## 认证方式

| 客户端类型 | 认证方式 | 说明 |
|-----------|---------|------|
| Web 端 | Cookie (httpOnly) | 与其他模块一致，通过 Token 中间件认证 |
| 外部设备 | API Token | 设备注册时生成，SM4 加密存储，请求头 `Authorization: Bearer <token>` |

WebSocket 连接同时支持两种认证方式，通过 `apps.common` 的 WebSocket 认证中间件统一处理。

---

## Redis 键说明

| 键模式 | 用途 | TTL |
|--------|------|-----|
| `voice:session:{uid}` | 语音会话状态（JSON，含会话阶段、当前说话人等） | `VOICE_SESSION_TTL`（默认 120s） |
| `voice:active_conv:{uid}` | 活跃对话标记（标识用户是否处于语音对话中） | `VOICE_ACTIVE_CONV_TTL`（默认 30s） |
| `voice:audio_chunks:{uid}:{seg}` | 音频分段缓冲（按 segment 编号存储 PCM 音频块） | `VOICE_AUDIO_CACHE_TTL`（默认 300s） |
| `voice:stt_pending:{uid}:{seg}` | STT 转写待处理标记 | 30s |
| `voice:stt_result:{uid}:{seg}` | STT 转写结果 | 3600s |
| `voice:llm_rate:{uid}` | LLM 调用频率限制（防止语音模式下过频调用） | 60s |
| `voice:recent_speakers:{uid}` | 最近识别的说话人缓存（SADD + EXPIRE，加速声纹识别） | 300s |
| `voice:ws_connect_rate:{uid}` | WebSocket 连接频率限制（INCR 计数器） | 60s |

---

## 频率限制

| 操作 | 限制 | 说明 |
|------|------|------|
| 声纹注册 | 5 次/小时 | `SpeakerRegistrationThrottle`（scope: `speaker_registration`），防止滥用 llmgateway 资源 |
| WebSocket 连接 | 10 次/分钟 | Redis INCR 计数器 `voice:ws_connect_rate:{uid}`，超限返回 4029 关闭码 |
| LLM 推理调用 | 60 次/分钟 | Redis 计数器 `voice:llm_rate:{uid}`，宪法 4.1 频率限制要求 |

---

## 关键工作流

### 语音聊天 WebSocket 完整流程

```
客户端                       VoiceConsumer                  llmgateway
  │                              │                              │
  ├─ ws connect ────────────────→│                              │
  │  (Cookie 或 device token)    ├─ ws connect ────────────────→│
  │                              │←──── session.created ────────┤
  ├─ session.configure ─────────→│                              │
  │                              ├─ session.configure ─────────→│
  │←── session.configured ───────┤←──── session.configured ─────┤
  │                              │                              │
  ├─ [Binary PCM16 音频帧] ─────→├─ [Binary PCM16 透传] ───────→│
  │                              │                              │
  │←── vad.speech_start ─────────┤←──── vad.speech_start ───────┤
  │   (含 segment_id)            │  (缓存音频 → Redis)          │
  │←── vad.speech_end ───────────┤←──── vad.speech_end ─────────┤
  │                              │  (启动异步 STT)              │
  │←── speaker.identified ───────┤←──── speaker.identified ─────┤
  │   (附加 user_id/user_name)   │  (查 SpeakerProfile 映射)    │
  │                              │                              │
  │←── response.start ───────────┤←──── response.start ─────────┤
  │←── response.delta (N次) ─────┤←──── response.delta ─────────┤
  │←── response.end ─────────────┤←──── response.end ───────────┤
  │                              │  (持久化消息 → chat.Message)  │
  │←── message.saved ────────────┤                              │
  │←── transcription.complete ───┤  (STT 结果就绪)              │
  │                              │                              │
  ├─ session.close ─────────────→│                              │
  │←── session.closed ───────────┤─ ws close ──────────────────→│
```

### 响应决策链优先级（短路求值，命中即返回）

| 优先级 | 条件 | 决策结果 |
|--------|------|----------|
| 1 | 紧急命令词白名单（停/取消/闭嘴/停止/别说了） | `STOP` |
| 2 | 唤醒词精确匹配（文本包含唤醒词） | `RESPOND` |
| 3 | 唤醒词模糊匹配（编辑距离 <=1 或拼音相似度 >=0.8） | `RESPOND` |
| 4 | 活跃对话状态（Redis `voice:active_conv:{uid}` 存在） | `RESPOND` |
| 5 | 非活跃 + 多 speaker 活跃（`voice:recent_speakers` SCARD >= 2） | `RECORD_ONLY` |
| 6 | 非活跃 + 单 speaker + 问句特征（问号/疑问词/句尾语气词） | `RESPOND` |
| 7 | 默认 | `RECORD_ONLY` |

---

## 异常体系

### SpeakerRegistrationError

声纹注册专用异常，在 `speaker_service.py` 中定义。由视图层捕获后统一返回 `SPEAKER_REGISTRATION_ERROR` 错误码。

### Gateway 错误码到宪法异常的映射

`gateway_client.py` 中的 `_GATEWAY_ERROR_MAP` 负责将 llmgateway 错误码映射到宪法 4.3 异常体系：

| Gateway 错误码 | 映射目标异常 | error_code | should_retry | max_retries |
|---------------|-------------|------------|-------------|-------------|
| `CONNECTION_FAILED` | `LLMConnectionError` | `LLM_CONNECTION_ERROR` | True | 3 |
| `CONNECT_TIMEOUT` | `LLMConnectionError` | `LLM_CONNECTION_ERROR` | True | 3 |
| `TIMEOUT` | `LLMTimeoutError` | `LLM_TIMEOUT` | True | 3 |
| `INFERENCE_TIMEOUT` | `LLMTimeoutError` | `LLM_TIMEOUT` | True | 3 |
| `RATE_LIMIT` | `LLMRateLimitError` | `LLM_RATE_LIMIT` | False | 0 |
| `RATE_LIMITED` | `LLMRateLimitError` | `LLM_RATE_LIMIT` | False | 0 |
| `CONTENT_FILTER` | `LLMContentFilterError` | `LLM_CONTENT_FILTER` | False | 0 |
| `CONTENT_BLOCKED` | `LLMContentFilterError` | `LLM_CONTENT_FILTER` | False | 0 |
| `INVALID_RESPONSE` | `LLMInvalidResponseError` | `LLM_INVALID_RESPONSE` | True | 3 |
| `MODEL_ERROR` | `LLMInvalidResponseError` | `LLM_INVALID_RESPONSE` | True | 3 |
| `CONTEXT_LENGTH` | `LLMContextLengthError` | `LLM_CONTEXT_LENGTH` | False | 0 |
| `CONTEXT_TOO_LONG` | `LLMContextLengthError` | `LLM_CONTEXT_LENGTH` | False | 0 |
| `INPUT_TOO_LONG` | `LLMContextLengthError` | `LLM_CONTEXT_LENGTH` | False | 0 |
| `QUOTA_EXCEEDED` | `LLMQuotaExceededError` | `LLM_QUOTA_EXCEEDED` | False | 0 |
| 其他未识别错误码 | `ExternalServiceError` | `EXTERNAL_SERVICE_ERROR` | False | 0 |

---

## 配置项依赖

以下为 `core/settings.py` 中语音模块依赖的配置项：

### llmgateway 连接配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_GATEWAY_WS_URL` | `ws://127.0.0.1:8888` | llmgateway WebSocket 端点 |
| `LLM_GATEWAY_WS_API_KEY` | （空） | llmgateway API 密钥 |
| `LLM_GATEWAY_HTTP_URL` | `http://127.0.0.1:8889` | llmgateway HTTP 端点（声纹注册/删除） |

### 语音会话配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VOICE_SESSION_TTL` | 120s | 会话状态 Redis TTL |
| `VOICE_ACTIVE_CONV_TTL` | 30s | 活跃对话标记 Redis TTL |
| `VOICE_AUDIO_CACHE_TTL` | 300s | 音频缓存 Redis TTL |
| `VOICE_MAX_RECORDING_SECONDS` | 30s | 单次最大录音时长 |
| `VOICE_IDLE_TIMEOUT` | 60s | 连接空闲超时（无消息则断开） |
| `VOICE_STT_TIMEOUT` | 30s | STT 转写超时 |

### 唤醒词与响应决策

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VOICE_DEFAULT_WAKE_WORDS` | `["小鱼"]` | 默认唤醒词列表 |
| `VOICE_SPEAKER_THRESHOLD` | 0.5 | 声纹识别阈值 |
| `VOICE_VAD_THRESHOLD` | 0.5 | VAD 阈值（0.0~1.0，越大越不灵敏） |
| `VOICE_WAKE_WORD_FUZZY_THRESHOLD` | 0.8 | 唤醒词拼音模糊匹配阈值 |

### Django Channels（WebSocket 传输层）

| 配置项 | 说明 |
|--------|------|
| `CHANNEL_LAYERS` | Redis DB3（独立于 DB0 缓存 / DB1 Langfuse / DB2 Celery Broker） |

---

## 关键依赖

| 依赖模块 | 用途 |
|---------|------|
| `apps.common` | WebSocket 认证中间件、异常体系、响应格式 |
| `apps.chat` | Message 模型（语音消息入库复用聊天消息表） |
| `apps.users` | SysUser 模型 + SM4 加密工具（设备 Token 加密） |
| `core.redis` | 异步/同步 Redis 客户端 |

---

## 测试方法

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 voice 测试
pytest tests/voice/ -v

# 单个文件
pytest tests/voice/test_speaker_service.py -v

# 带覆盖率
pytest tests/voice/ --cov=apps/voice --cov-report=term-missing
```

---

## 注意事项与约束

1. WebSocket Consumer 使用 Django Channels，必须通过 ASGI 模式启动（uvicorn）
2. 所有数据操作按 `user_id` 粒度隔离，不存在会话粒度
3. 设备 API Token 使用 SM4 加密存储，禁止明文存储
4. 音频数据通过 Redis 缓冲，避免大量音频数据直接写入数据库
5. 响应决策服务的三种结果（RESPOND / RECORD_ONLY / STOP）决定了 Agent 是否被触发


<claude-mem-context>

</claude-mem-context>