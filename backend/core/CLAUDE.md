# core 模块指南

> Django 项目核心配置模块，包含全局设置、路由、ASGI/WSGI 入口、Celery 配置和 Redis 客户端封装。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `settings.py` | Django 全局配置（数据库、Redis、LLM、媒体、Celery、Langfuse、安全等） |
| `urls.py` | 顶层 URL 路由分发（`api/v1/` 前缀） |
| `asgi.py` | ASGI 应用入口（uvicorn 使用，必须用此启动） |
| `wsgi.py` | WSGI 应用入口（已废弃，禁止使用） |
| `celery.py` | Celery 应用配置 + Beat 定时任务调度 |
| `redis.py` | 异步/同步 Redis 客户端封装 + 键名工具 + Pub/Sub 频道 |
| `__init__.py` | 导入 Celery app 确保启动时注册 |

---

## 路由分发 (urls.py)

| 前缀 | 目标模块 | 说明 |
|------|---------|------|
| `admin/` | Django Admin | 后台管理 |
| `api/v1/auth/` | `apps.users.urls` | 认证（验证码、登录/登出、用户信息） |
| `api/v1/chat/` | `apps.chat.urls` | 聊天（消息、流式、媒体、文档解析、推理取消） |
| `api/v1/models/` | `apps.models.urls` | 模型配置管理 |
| `api/v1/memories/` | `apps.memory.urls` | 记忆 CRUD + 搜索 |
| `api/v1/voice/` | `apps.voice.urls` | 语音交互（声纹、设备、设置） |
| `api/v1/` (无前缀) | `apps.common.urls` | SSE 事件流（`/api/v1/events`） |

---

## 关键配置项 (settings.py)

### INSTALLED_APPS

```python
"apps.common", "apps.users", "apps.chat", "apps.models", "apps.memory", "apps.graph", "apps.context", "apps.voice", "channels"
```

第三方: `rest_framework`, `corsheaders`, `django_celery_beat`, `django.contrib.postgres`

### 数据库

- PostgreSQL，从 `DATABASE_URL` 环境变量解析
- 连接池: `CONN_MAX_AGE=60`, `connect_timeout=10`

### Redis

- 缓存: DB0（`REDIS_URL`），django-redis 后端，最大连接 50
- Celery Broker: DB2（`CELERY_BROKER_URL`）
- Channels: DB3（Django Channels）

### LLM 超时与重试

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `LLM_CALL_TIMEOUT` | 60s | 单次 LLM 调用超时 |
| `AGENT_TOTAL_TIMEOUT` | 300s | Agent 总超时 |
| `SUBAGENT_TIMEOUT` | 60s | SubAgent 单次超时 |
| `LLM_MAX_RETRIES` | 3 | 最大重试次数 |
| `LLM_INITIAL_RETRY_DELAY` | 1.0s | 初始重试延迟 |
| `LLM_MAX_RETRY_DELAY` | 8.0s | 最大重试延迟 |
| `LLM_RETRY_BACKOFF` | 2.0 | 退避倍数 |

### LLM Gateway（多模态/文档解析）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8100` | Gateway 基础地址 |
| `LLM_GATEWAY_API_KEY` | - | Gateway API 密钥 |
| `LLM_GATEWAY_INFERENCE_TIMEOUT` | 180s | 推理请求超时 |
| `LLM_GATEWAY_CANCEL_TIMEOUT` | 5s | 取消请求超时 |
| `LLM_GATEWAY_POLL_TIMEOUT` | 30s | 轮询查询超时 |
| `LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT` | 480s | 文档解析创建超时（含模型切换） |
| `LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT` | 30s | 文档解析结果获取超时 |
| `LLM_GATEWAY_GUARDRAILS_LEVEL` | fast | 护栏级别 |

### LLM Gateway WebSocket

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `LLM_GATEWAY_WS_URL` | `ws://127.0.0.1:8888` | Gateway WebSocket 地址 |
| `LLM_GATEWAY_WS_API_KEY` | - | WebSocket API 密钥 |

### 文档解析

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `DOC_PARSE_MAX_FILE_SIZE` | 10MB | 最大文件大小 |
| `DOC_PARSE_MAX_PAGES` | 200 | 最大页数 |
| `DOC_PARSE_POLL_INTERVAL` | 3s | 轮询间隔 |
| `DOC_PARSE_POLL_MAX_WAIT` | 900s | 最大等待时间 |
| `DOC_PARSE_DEFAULT_MODEL` | minicpm-o | 默认解析模型 |
| `DOC_PARSE_MAX_RESULT_LENGTH` | 8000 | 结果截断长度 |

### MinIO 对象存储

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MINIO_ENDPOINT` | `localhost:9010` | MinIO 端点 |
| `MINIO_BUCKET_MEDIA` | `linchat-media` | 媒体文件桶 |
| `MINIO_BUCKET_THUMBNAILS` | `linchat-thumbnails` | 缩略图桶 |

### 媒体文件限制

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MEDIA_MAX_IMAGE_SIZE` | 10MB | 图片最大大小 |
| `MEDIA_MAX_VIDEO_SIZE` | 50MB | 视频最大大小 |
| `MEDIA_MAX_AUDIO_SIZE` | 10MB | 音频最大大小 |
| `MEDIA_MAX_DOCUMENT_SIZE` | 10MB | 文档最大大小 |
| `MEDIA_MAX_DURATION_SECONDS` | 60s | 音视频最大时长 |
| `MEDIA_MAX_ATTACHMENTS` | 5 | 单次最多附件数 |
| `MEDIA_EXPIRY_DAYS` | 7 | 媒体文件过期天数 |

### 多模态推理

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MULTIMODAL_MAX_TOKENS` | 1024 | 多模态最大输出 token |
| `MULTIMODAL_RATE_LIMIT_SECONDS` | 60s | 多模态限流间隔 |
| `MULTIMODAL_SUBAGENT_TIMEOUT` | 1200s (20min) | 多模态 SubAgent 超时 |
| `GPU_LOCK_MAX_WAIT` | 600s (10min) | 等待 GPU 锁上限 |
| `AGENT_MULTIMODAL_TIMEOUT` | 1500s (25min) | 含文档附件时 Agent 总超时 |
| `VIDEO_PREPROCESS_WIDTH` | 320px | 视频预处理最大宽度 |

### 语音交互

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `VOICE_SESSION_TTL` | 120s | 会话状态 TTL |
| `VOICE_ACTIVE_CONV_TTL` | 30s | 活跃对话 TTL |
| `VOICE_AUDIO_CACHE_TTL` | 300s | 音频缓存 TTL |
| `VOICE_MAX_RECORDING_SECONDS` | 30s | 最大录音时长 |
| `VOICE_IDLE_TIMEOUT` | 60s | 连接空闲超时 |
| `VOICE_STT_TIMEOUT` | 30s | STT 转写超时 |
| `VOICE_DEFAULT_WAKE_WORDS` | `["小鱼"]` | 默认唤醒词 |
| `VOICE_SPEAKER_THRESHOLD` | 0.5 | 声纹识别阈值 |
| `VOICE_VAD_THRESHOLD` | 0.5 | VAD 灵敏度默认值 |
| `VOICE_WAKE_WORD_FUZZY_THRESHOLD` | 0.8 | 唤醒词模糊匹配阈值 |

### 认证相关

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `AUTH_TOKEN_IDLE_TTL` | 3600s | Token 无操作过期 |
| `AUTH_TOKEN_ABSOLUTE_TTL` | 86400s | Token 绝对过期 |
| `AUTH_CAPTCHA_TTL` | 120s | 验证码有效期 |
| `AUTH_MAX_FAIL_COUNT` | 5 | 最大登录失败次数 |
| `AUTH_LOCK_DURATION` | 900s | 账户锁定时间 |

### Memory 业务配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MEMORY_EMBEDDING_DIMENSION` | 1024 | Embedding 维度 |
| `MEMORY_SEARCH_TOP_K` | 5 | 搜索返回数量 |
| `MEMORY_VECTOR_WEIGHT` | 0.7 | 向量搜索权重 |
| `MEMORY_KEYWORD_WEIGHT` | 0.3 | 关键词搜索权重 |

### 上下文

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `CONTEXT_HISTORY_ROUNDS` | 10 | 默认保留最近对话轮数 |
| `MAX_TOOL_RESULT_TOKENS` | 1500 | 工具结果最大 token 数 |
| `MAX_MESSAGE_LENGTH` | 4000 | 用户消息最大长度 |

### Home Assistant

| 配置 | 说明 |
|------|------|
| `HA_URL` | HA 实例地址 |
| `HA_TOKEN` | Long-Lived Access Token |
| `HA_REQUEST_TIMEOUT` | HTTP 请求超时（默认 10s） |
| `HA_BLOCKED_ENTITIES` | 黑名单设备列表 |
| `HA_ENABLED` | 有配置才启用（自动判断） |

### 安全配置

- httpOnly Cookie + CSRF Cookie 保护
- `SECURE_BROWSER_XSS_FILTER`, `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS=DENY`
- SM4 密钥: `SM4_SECRET_KEY`（必须 16 字节）
- DRF 限流: 匿名 100/时, 认证 1000/时

---

## Celery 配置 (celery.py)

- **Broker**: Redis DB2（与缓存 DB0 / Langfuse DB1 隔离）
- **序列化**: JSON
- **时区**: Asia/Shanghai

### Beat 定时任务

| 任务 | 调度 | 说明 |
|------|------|------|
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆总结 |
| `memory.generate_monthly_summary` | 每月 1 日 00:00 | 每月记忆总结 |
| `memory.embedding_health_check` | 每小时整点 | Embedding 健康检查 |
| `chat.clean_expired_media` | 每日凌晨 3:00 | 清理过期媒体文件 |

---

## Redis 工具 (redis.py)

### 客户端

| 类/函数 | 用途 |
|---------|------|
| `RedisClient` (异步) | ASGI 异步视图使用，每次创建新连接避免事件循环问题 |
| `SyncRedisClient` (同步) | 同步中间件使用，单例模式 |
| `get_redis()` | 获取异步 Redis 客户端 |

### 便捷方法

```python
# 异步方法
await redis_get(key)               # 获取字符串
await redis_set(key, value, ex=N)  # 设置字符串
await redis_setex(key, seconds, value)
await redis_delete(key)
await redis_expire(key, seconds)
await redis_ttl(key)
await redis_exists(key)
await redis_get_json(key)          # JSON 序列化
await redis_set_json(key, value, ex=N)
await redis_setex_json(key, seconds, value)

# 同步方法
sync_redis_get(key)
sync_redis_delete(key)
sync_redis_expire(key, seconds)
```

### 键名工具函数

| 函数 | 键模式 | 用途 |
|------|--------|------|
| `get_token_key(token_hash)` | `auth:token:{hash}` | Token 数据 |
| `get_user_token_key(user_id)` | `auth:user_token:{id}` | 用户当前 Token 索引（SSO） |
| `get_captcha_key(captcha_id)` | `auth:captcha:{id}` | 验证码文本 |
| `get_login_fail_key(username)` | `auth:fail:{name}` | 登录失败计数 |
| `get_user_events_channel(user_id)` | `events:user:{id}` | SSE 事件 Pub/Sub 频道 |

---

## Django Channels

- `CHANNEL_LAYERS` 使用 Redis DB3（`channels.layers.RedisChannelLayer`）
- ASGI 应用入口通过 `ProtocolTypeRouter` 增加 WebSocket 路由，区分 HTTP 和 WebSocket 协议

---

## 日志配置

| Logger | 级别 | 说明 |
|--------|------|------|
| `root` | INFO | 默认 |
| `django` | INFO (可通过 `DJANGO_LOG_LEVEL` 调整) | Django 框架日志 |
| `apps` | DEBUG(调试)/INFO(生产) | 应用业务日志 |
| `apps.context.monitoring` | DEBUG | 上下文监控（始终 DEBUG） |
