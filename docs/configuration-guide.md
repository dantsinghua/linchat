# LinChat 配置指南

> 本文档是 LinChat 平台所有配置项的完整参考手册。
> 涵盖后端、前端、Docker、Nginx、Redis、Celery、LLM Gateway、安全等全部配置。

---

## 目录

1. [配置文件总览](#1-配置文件总览)
2. [环境变量参考](#2-环境变量参考)
3. [后端配置详解](#3-后端配置详解)
4. [前端配置详解](#4-前端配置详解)
5. [Docker 配置](#5-docker-配置)
6. [Nginx 反向代理配置](#6-nginx-反向代理配置)
7. [Redis 数据库分配](#7-redis-数据库分配)
8. [Celery 定时任务](#8-celery-定时任务)
9. [LLM Gateway 配置](#9-llm-gateway-配置)
10. [安全配置](#10-安全配置)
11. [参考文档](#11-参考文档)

---

## 1. 配置文件总览

LinChat 使用三层环境变量文件，分别服务于不同的运行时组件：

| 文件路径 | 作用范围 | 说明 |
|----------|----------|------|
| `.env` (项目根目录) | Docker Compose 服务 | PostgreSQL、Redis、ClickHouse、MinIO、Langfuse 的容器级配置 |
| `backend/.env` | Django 后端应用 | 数据库连接、Redis、LLM、加密密钥、Langfuse API、媒体、语音等全部后端配置 |
| `frontend/.env.local` | Next.js 前端应用 | API 地址、SM4 加密密钥、功能开关 |

### 配置加载机制

- **后端**: Django `settings.py` 通过 `python-dotenv` 加载 `backend/.env`，所有配置项均提供合理默认值
- **前端**: Next.js 自动加载 `frontend/.env.local`，`NEXT_PUBLIC_` 前缀的变量暴露给客户端
- **Docker**: `docker-compose.yml` 通过 `${VAR:-default}` 语法引用根目录 `.env`

### 配置文件模板

项目提供了两个示例文件，首次部署时复制并修改：

```bash
# 根目录 Docker 配置
cp .env.example .env

# 前端配置
cp frontend/.env.local.example frontend/.env.local

# 后端配置需手动创建 backend/.env（参考本文档第 2 节）
```

---

## 2. 环境变量参考

### 2.1 根目录 `.env` (Docker Compose)

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `POSTGRES_USER` | `postgres` | 否 | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | `linchat_123` | **是** | PostgreSQL 密码 |
| `REDIS_PASSWORD` | `redis_linchat_123` | **是** | Redis 认证密码 |
| `CLICKHOUSE_PASSWORD` | `langfuse_ch_123` | 否 | ClickHouse 密码 (Langfuse 专用) |
| `MINIO_ROOT_USER` | `minioadmin` | 否 | MinIO 管理员用户名 |
| `MINIO_ROOT_PASSWORD` | `minio_123_secure` | **是** | MinIO 管理员密码 |
| `LANGFUSE_NEXTAUTH_SECRET` | 无 | **是** | Langfuse NextAuth 签名密钥 (>=32 字符) |
| `LANGFUSE_SALT` | 无 | **是** | Langfuse 加密盐值 (>=32 字符) |
| `LANGFUSE_ENCRYPTION_KEY` | 无 | **是** | Langfuse 数据加密密钥 |
| `LANGFUSE_INIT_ORG_ID` | `linchat-org` | 否 | Langfuse 初始组织 ID |
| `LANGFUSE_INIT_ORG_NAME` | `LinChat` | 否 | Langfuse 初始组织名称 |
| `LANGFUSE_INIT_PROJECT_ID` | `linchat-project` | 否 | Langfuse 初始项目 ID |
| `LANGFUSE_INIT_PROJECT_NAME` | `linchat-monitor` | 否 | Langfuse 初始项目名称 |
| `LANGFUSE_INIT_USER_EMAIL` | `admin@linchat.local` | 否 | Langfuse 管理员邮箱 |
| `LANGFUSE_INIT_USER_PASSWORD` | `Admin@123456` | 否 | Langfuse 管理员密码 |
| `LANGFUSE_INIT_USER_NAME` | `Admin` | 否 | Langfuse 管理员显示名 |

### 2.2 后端 `backend/.env`

#### 核心基础设施

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `DATABASE_URL` | `postgresql://postgres:linchat_123@localhost:5432/linchat` | **是** | PostgreSQL 连接字符串 |
| `REDIS_URL` | `redis://:redis_linchat_123@localhost:6379/0` | **是** | Redis DB0 连接字符串 (缓存) |
| `DJANGO_SECRET_KEY` | `django-insecure-dev-key-...` | **是** | Django 密钥 (生产环境必须更换) |
| `DJANGO_DEBUG` | `true` | 否 | 调试模式 (生产环境设为 `false`) |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | **是** | 允许的主机名 (逗号分隔) |
| `DJANGO_LOG_LEVEL` | `INFO` | 否 | Django 日志级别 |

#### CORS 与安全

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | **是** | CORS 允许的源 (逗号分隔) |
| `SM4_SECRET_KEY` | `default-sm4-key-16` | **是** | 国密 SM4 加密密钥 (必须 16 字节) |

#### LLM 调用超时与重试

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `LLM_CALL_TIMEOUT` | `60` | 否 | 单次 LLM 调用超时 (秒) |
| `AGENT_TOTAL_TIMEOUT` | `300` | 否 | Agent 总执行超时 (秒) |
| `LLM_MAX_RETRIES` | `3` | 否 | LLM 调用最大重试次数 |
| `SUBAGENT_TIMEOUT` | `60` | 否 | SubAgent 单次执行超时 (秒) |
| `LLM_INITIAL_RETRY_DELAY` | `1.0` | 否 | 初始重试延迟 (秒) |
| `LLM_MAX_RETRY_DELAY` | `8.0` | 否 | 最大重试延迟 (秒) |
| `LLM_RETRY_BACKOFF` | `2.0` | 否 | 指数退避倍数 |

#### LLM Gateway

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8100` | **是** | LLM Gateway HTTP 基础地址 |
| `LLM_GATEWAY_API_KEY` | 空 | 否 | Gateway 认证密钥 |
| `LLM_GATEWAY_TIMEOUT` | `180` | 否 | 通用网关超时 (秒) |
| `LLM_GATEWAY_INFERENCE_TIMEOUT` | `180` | 否 | 推理请求超时 (秒) |
| `LLM_GATEWAY_CANCEL_TIMEOUT` | `5` | 否 | 取消请求超时 (秒) |
| `LLM_GATEWAY_POLL_TIMEOUT` | `30` | 否 | 轮询查询超时 (秒) |
| `LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT` | `480` | 否 | 文档解析创建超时 (秒) |
| `LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT` | `30` | 否 | 文档解析结果查询超时 (秒) |
| `LLM_GATEWAY_GUARDRAILS_LEVEL` | `fast` | 否 | 护栏检查级别 |

#### Langfuse 监控

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `LANGFUSE_PUBLIC_KEY` | 空 | **是** | Langfuse 公钥 |
| `LANGFUSE_SECRET_KEY` | 空 | **是** | Langfuse 私钥 |
| `LANGFUSE_HOST` | `http://localhost:3001` | 否 | Langfuse 服务地址 |

#### Celery 任务队列

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `CELERY_BROKER_URL` | `redis://:redis_linchat_123@localhost:6379/2` | **是** | Celery Broker (Redis DB2) |
| `CELERY_RESULT_BACKEND` | `redis://:redis_linchat_123@localhost:6379/2` | **是** | Celery 结果后端 (Redis DB2) |

#### Memory (记忆系统)

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MEMORY_EMBEDDING_PENDING_TIMEOUT` | `300` | 否 | Embedding pending 超时 (秒) |
| `MEMORY_CONTENT_MAX_LENGTH` | `10000` | 否 | 记忆内容最大长度 |
| `MEMORY_EMBEDDING_DIMENSION` | `1024` | 否 | Embedding 向量维度 |
| `MEMORY_SEARCH_TOP_K` | `5` | 否 | 搜索返回结果数 |
| `MEMORY_VECTOR_WEIGHT` | `0.7` | 否 | 混合搜索中向量权重 |
| `MEMORY_KEYWORD_WEIGHT` | `0.3` | 否 | 混合搜索中关键词权重 |
| `MEMORY_EMBEDDING_MAX_RETRY` | `3` | 否 | Embedding 最大重试次数 |
| `COMPRESS_LOCK_TIMEOUT` | `60` | 否 | 压缩锁超时 (秒) |

#### 上下文与监控

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAX_TOOL_RESULT_TOKENS` | `1500` | 否 | 工具结果最大 token 数 |
| `MONITOR_PUSH_INTERVAL` | `0.5` | 否 | 监控数据推送间隔 (秒) |
| `MAX_MESSAGE_LENGTH` | `4000` | 否 | 单条消息最大长度 |
| `CONTEXT_HISTORY_ROUNDS` | `10` | 否 | 保留最近对话轮数 |
| `SSE_HEARTBEAT_INTERVAL` | `15` | 否 | SSE 心跳间隔 (秒) |

#### MinIO 对象存储

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MINIO_ENDPOINT` | `localhost:9010` | **是** | MinIO 端点地址 |
| `MINIO_ACCESS_KEY` | 空 | **是** | MinIO 访问密钥 |
| `MINIO_SECRET_KEY` | 空 | **是** | MinIO 私密密钥 |
| `MINIO_SECURE` | `false` | 否 | 是否使用 HTTPS |
| `MINIO_BUCKET_MEDIA` | `linchat-media` | 否 | 媒体文件存储桶 |
| `MINIO_BUCKET_THUMBNAILS` | `linchat-thumbnails` | 否 | 缩略图存储桶 |

#### 媒体文件限制

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MEDIA_MAX_IMAGE_SIZE` | `10485760` (10MB) | 否 | 图片最大大小 (字节) |
| `MEDIA_MAX_VIDEO_SIZE` | `52428800` (50MB) | 否 | 视频最大大小 (字节) |
| `MEDIA_MAX_AUDIO_SIZE` | `10485760` (10MB) | 否 | 音频最大大小 (字节) |
| `MEDIA_MAX_DOCUMENT_SIZE` | `10485760` (10MB) | 否 | 文档最大大小 (字节) |
| `MEDIA_MAX_DURATION_SECONDS` | `60` | 否 | 媒体最大时长 (秒) |
| `MEDIA_MAX_ATTACHMENTS` | `5` | 否 | 单次最大附件数 |
| `MEDIA_EXPIRY_DAYS` | `7` | 否 | 媒体文件过期天数 |

#### 多模态推理

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MULTIMODAL_MAX_TOKENS` | `1024` | 否 | 多模态推理最大输出 token |
| `MULTIMODAL_RATE_LIMIT_SECONDS` | `60` | 否 | 多模态推理限流间隔 (秒) |
| `MULTIMODAL_SUBAGENT_TIMEOUT` | `1200` | 否 | 多模态 SubAgent 超时 (秒) |
| `GPU_LOCK_MAX_WAIT` | `600` | 否 | GPU 锁等待上限 (秒) |
| `AGENT_MULTIMODAL_TIMEOUT` | `2400` | 否 | 含文档附件时 Agent 总超时 (秒) |

#### 文档解析

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `DOC_PARSE_MAX_FILE_SIZE` | `10485760` (10MB) | 否 | 文档最大文件大小 |
| `DOC_PARSE_MAX_PAGES` | `200` | 否 | 文档最大页数 |
| `DOC_PARSE_POLL_INTERVAL` | `3` | 否 | 轮询间隔 (秒) |
| `DOC_PARSE_POLL_MAX_WAIT` | `900` | 否 | 最大等待时间 (秒) |
| `DOC_PARSE_DEFAULT_MODEL` | `minicpm-o` | 否 | 默认解析模型 |
| `DOC_PARSE_MAX_RESULT_LENGTH` | `6000` | 否 | 解析结果最大字符数 |
| `VIDEO_PREPROCESS_WIDTH` | `320` | 否 | 视频预处理最大宽度 (px) |

#### 文档 RAG

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `DOCUMENT_SUBAGENT_TIMEOUT` | `1200` | 否 | 文档 SubAgent 超时 (秒) |
| `DOC_CHUNK_SIZE` | `800` | 否 | 文档分块大小 (字符) |
| `DOC_CHUNK_OVERLAP` | `100` | 否 | 分块重叠 (字符) |
| `DOC_VECTOR_WEIGHT` | `0.7` | 否 | 文档混合搜索向量权重 |
| `DOC_KEYWORD_WEIGHT` | `0.3` | 否 | 文档混合搜索关键词权重 |
| `DOC_SEARCH_TOP_K` | `5` | 否 | 文档搜索结果上限 |

#### 推理任务

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `INFERENCE_TASK_TTL` | `300` | 否 | 推理任务 TTL (秒) |

#### Home Assistant 集成

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `HA_URL` | 空 | 否 | Home Assistant 实例地址 |
| `HA_TOKEN` | 空 | 否 | HA Long-Lived Access Token |
| `HA_REQUEST_TIMEOUT` | `10` | 否 | HA HTTP 请求超时 (秒) |
| `HA_BLOCKED_ENTITIES` | 空 | 否 | 黑名单设备列表 (逗号分隔) |

#### Brave Search

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `BRAVE_SEARCH_API_KEY` | 空 | 否 | Brave Search API 密钥 |

#### 语音交互 (Gateway 端点)

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_ASR_WS_URL` | `ws://127.0.0.1:8100/v1/audio/transcriptions/stream` | 否 | ASR WebSocket 端点 |
| `VOICE_TTS_URL` | `ws://127.0.0.1:8100/v1/audio/speech/stream` | 否 | TTS WebSocket 端点 |
| `VOICE_TTS_ENABLED` | `true` | 否 | 是否启用 TTS |
| `VOICE_TTS_VOICE` | `zf_xiaobei` | 否 | TTS 音色 |
| `VOICE_TTS_TIMEOUT` | `30` | 否 | TTS 完成超时 (秒) |

#### 语音 TTS 播报队列

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_TTS_COMFORT_DELAY` | `3.0` | 否 | 安慰语音触发延迟 (秒) |
| `VOICE_TTS_SEGMENT_GAP` | `1.0` | 否 | 播报段间静默 (秒) |
| `VOICE_TTS_COMFORT_TEXTS` | `["正在思考..."]` | 否 | 安慰语音文本列表 (JSON 数组) |
| `VOICE_TTS_ERROR_TEXT` | `大模型调用失败了...` | 否 | 错误提示语音文本 |

#### 语音 ASR 参数

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_ASR_SPEECH_PAD_MS` | `2000` | 否 | 语音填充毫秒数 |
| `VOICE_ASR_LANGUAGE` | `zh` | 否 | ASR 语言 |
| `VOICE_MAX_SEGMENT_DURATION` | `60` | 否 | 单段语音最大时长 (秒) |

#### 语音会话管理

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_SESSION_TTL` | `120` | 否 | 语音会话状态 TTL (秒) |
| `VOICE_ACTIVE_CONV_TTL` | `30` | 否 | 活跃对话 TTL (秒) |
| `VOICE_AUDIO_CACHE_TTL` | `300` | 否 | 音频缓存 TTL (秒) |
| `VOICE_MAX_RECORDING_SECONDS` | `30` | 否 | 最大录音时长 (秒) |
| `VOICE_IDLE_TIMEOUT` | `60` | 否 | 连接空闲超时 (秒) |
| `VOICE_STT_TIMEOUT` | `30` | 否 | STT 转写超时 (秒) |

#### 语音唤醒与 VAD

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_SPEAKER_THRESHOLD` | `0.5` | 否 | 声纹识别阈值 (0.0~1.0) |
| `VOICE_VAD_THRESHOLD` | `0.5` | 否 | VAD 阈值 (越大越不灵敏) |
| `VOICE_WAKE_WORD_FUZZY_THRESHOLD` | `0.8` | 否 | 唤醒词拼音模糊匹配阈值 |

#### 环境语音模式 (Ambient)

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_AMBIENT_AGGREGATE_TIMEOUT` | `3.0` | 否 | 话语聚合静默超时 (秒) |
| `VOICE_AMBIENT_MAX_BUFFER_SIZE` | `10` | 否 | 聚合缓冲区最大话语数 |
| `VOICE_AMBIENT_SESSION_TTL` | `3600` | 否 | Ambient 会话 TTL (秒) |
| `VOICE_AMBIENT_RECORD_ONLY_LIMIT` | `20` | 否 | RECORD_ONLY 消息保留上限 |
| `VOICE_DECISION_USE_LLM` | `false` | 否 | 是否启用 LLM 意图分类 |
| `VOICE_DECISION_LLM_THRESHOLD` | `0.7` | 否 | LLM 分类置信度阈值 |
| `VOICE_DECISION_LLM_TIMEOUT` | `1.0` | 否 | LLM 分类超时 (秒) |

#### 声纹 Diarize

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `VOICE_SPEAKER_MIN_AUDIO_SECONDS` | `1.0` | 否 | 声纹最短音频时长 (秒) |
| `VOICE_DIARIZE_TIMEOUT` | `15.0` | 否 | Diarize 超时 (秒) |
| `VOICE_DIARIZE_MATCH_THRESHOLD` | `0.6` | 否 | 说话人匹配阈值 |
| `VOICE_DIARIZE_CLUSTER_THRESHOLD` | `0.4` | 否 | 聚类阈值 |

#### Django Channels (WebSocket)

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `CHANNELS_REDIS_URL` | `redis://:redis_linchat_123@localhost:6379/3` | 否 | Channels Redis 连接 (DB3) |

### 2.3 前端 `frontend/.env.local`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000/api/v1` | **是** | 后端 API 基础 URL |
| `NEXT_PUBLIC_SM4_KEY` | 无 | **是** | SM4 加密密钥 (必须与后端一致) |
| `NEXT_PUBLIC_DEV_TOOLS` | `true` | 否 | 启用开发者工具 |
| `NEXT_PUBLIC_SSE_RECONNECT_INTERVAL` | `3000` | 否 | SSE 重连间隔 (毫秒) |
| `NEXT_PUBLIC_DEBUG` | `true` | 否 | 启用详细日志 |

> **注意**: `NEXT_PUBLIC_` 前缀的变量会被打包进客户端代码，不要在其中存放敏感信息。

---

## 3. 后端配置详解

后端配置集中在 `backend/core/settings.py`（507 行），通过 `python-dotenv` 从 `backend/.env` 加载环境变量。

### 3.1 数据库配置

```python
# 连接字符串格式
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>

# 连接池
CONN_MAX_AGE = 60          # 连接复用 60 秒
connect_timeout = 10       # 连接超时 10 秒
```

PostgreSQL 为唯一可信数据来源。LinChat 使用自定义镜像 `docker/postgres/Dockerfile`，内置 `pgvector` 和 `pg_jieba` 扩展，支持 1024 维向量搜索和中文分词。

### 3.2 REST Framework 配置

```python
REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",     # 匿名用户 100 次/小时
        "user": "1000/hour",    # 认证用户 1000 次/小时
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 20,
}
```

- 认证方式: 自定义 Token 认证（`TokenAuthMiddleware`），Token 存储在 httpOnly Cookie
- 异常处理: 自定义 `custom_exception_handler`，统一响应格式 `{"code": "...", "message": "...", "data": ...}`

### 3.3 LangGraph Checkpoint

```python
LANGGRAPH_CHECKPOINT_TTL = 1440    # 24 小时 (单位: 分钟)
LANGGRAPH_CHECKPOINT_REFRESH_ON_READ = True  # 读取时刷新 TTL
```

LangGraph 使用 Redis 存储 Checkpoint 状态，24 小时后自动过期。

### 3.4 认证参数

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `AUTH_TOKEN_IDLE_TTL` | 3600 秒 | Token 无操作过期时间 (1 小时) |
| `AUTH_TOKEN_ABSOLUTE_TTL` | 86400 秒 | Token 绝对过期时间 (24 小时) |
| `AUTH_CAPTCHA_TTL` | 120 秒 | 验证码有效期 (2 分钟) |
| `AUTH_FAIL_COUNT_TTL` | 900 秒 | 失败计数窗口 (15 分钟) |
| `AUTH_MAX_FAIL_COUNT` | 5 次 | 最大连续失败次数 |
| `AUTH_LOCK_DURATION` | 900 秒 | 账户锁定时长 (15 分钟) |

认证流程: 获取验证码 -> 填写用户名/密码/验证码 -> 后端验证 -> 签发 Token 写入 httpOnly Cookie。

### 3.5 日志配置

```python
LOGGING = {
    "loggers": {
        "django": {"level": "INFO"},       # Django 框架日志
        "apps": {"level": "DEBUG/INFO"},    # 应用日志 (DEBUG 模式下为 DEBUG)
        "apps.context.monitoring": {"level": "DEBUG"},  # 监控模块始终 DEBUG
    },
}
```

> **重要**: 后端通过 nohup 启动时必须设置 `PYTHONUNBUFFERED=1`，否则日志缓冲会导致异常 traceback 丢失。

### 3.6 Django 文件上传

```python
FILE_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024   # 60MB (超此大小写临时文件)
DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024   # 60MB (请求体最大大小)
```

---

## 4. 前端配置详解

### 4.1 Next.js 配置

前端通过 `next.config.mjs` 配置：

- **basePath**: `/linchat` — 所有路由前缀
- **output**: `standalone` — 独立部署模式
- **端口**: 3784（通过 `npm run start -- -p 3784` 指定）

### 4.2 API 地址

生产环境使用 Nginx 反向代理，前端 API 地址配置为相对路径：

```bash
# frontend/.env.local (生产环境)
NEXT_PUBLIC_API_BASE_URL=/linchat/api/v1
```

请求经 Nginx 路由 `/linchat/api/*` 转发到后端 8002 端口。

### 4.3 SM4 加密

前后端共享 SM4 密钥，用于密码和 API Key 的传输加密：

```bash
# 前端
NEXT_PUBLIC_SM4_KEY=linchat-sm4-key!

# 后端
SM4_SECRET_KEY=linchat-sm4-key!
```

两端密钥必须完全一致，否则解密失败。

---

## 5. Docker 配置

### 5.1 服务清单

`docker-compose.yml` 定义了以下服务：

| 服务名 | 容器名 | 镜像 | 端口映射 | 说明 |
|--------|--------|------|----------|------|
| `postgres` | linchat-postgres | 自定义 (pgvector+pg_jieba) | 5432:5432 | PostgreSQL 主数据库 |
| `redis` | linchat-redis | redis/redis-stack-server:latest | 6379:6379 | Redis 缓存 + 搜索 |
| `clickhouse` | linchat-clickhouse | clickhouse/clickhouse-server:24.3 | 127.0.0.1:8123/9000 | ClickHouse (Langfuse) |
| `minio` | linchat-minio | minio/minio:latest | 127.0.0.1:9010/9011 | MinIO 对象存储 |
| `minio-init` | linchat-minio-init | minio/mc:latest | 无 | MinIO 初始化 (创建 bucket) |
| `langfuse-web` | linchat-langfuse-web | langfuse/langfuse:3 | 127.0.0.1:3100:3000 | Langfuse Web UI |
| `langfuse-worker` | linchat-langfuse-worker | langfuse/langfuse-worker:3 | 无 | Langfuse 后台 Worker |
| `homeassistant` | linchat-homeassistant | ghcr.io/home-assistant/home-assistant:stable | 8124:8123 | Home Assistant |
| `nodered` | linchat-nodered | nodered/node-red:latest | 127.0.0.1:1880 | Node-RED 自动化 |

### 5.2 数据持久化

所有有状态服务使用 Docker Named Volume：

| Volume 名称 | 用途 |
|-------------|------|
| `postgres_data` | PostgreSQL 数据 |
| `redis_data` | Redis 持久化数据 |
| `clickhouse_data` | ClickHouse 数据 |
| `clickhouse_logs` | ClickHouse 日志 |
| `minio_data` | MinIO 对象存储 |
| `homeassistant_config` | HA 配置 |
| `nodered_data` | Node-RED 数据 |

### 5.3 健康检查

所有核心服务均配置了健康检查：

| 服务 | 检查方式 | 间隔/超时/重试 |
|------|----------|---------------|
| PostgreSQL | `pg_isready -U postgres` | 10s / 5s / 5 |
| Redis | `redis-cli ping` | 10s / 5s / 5 |
| ClickHouse | `wget --spider http://localhost:8123/ping` | 10s / 5s / 5 |
| MinIO | `curl http://localhost:9000/minio/health/live` | 10s / 5s / 5 |
| Home Assistant | `curl http://localhost:8123/` (200/401/403) | 30s / 10s / 3 |

### 5.4 网络

所有容器连接到 `linchat-network` Bridge 网络，容器间通过服务名直接通信。

### 5.5 常用命令

```bash
cd /home/dantsinghua/work/linchat

docker compose up -d              # 启动全部服务
docker compose ps                 # 查看运行状态
docker compose logs -f postgres   # 查看 PostgreSQL 日志
docker compose restart redis      # 重启 Redis
docker compose down               # 停止全部服务
```

---

## 6. Nginx 反向代理配置

### 6.1 配置文件

配置文件路径: `/etc/nginx/sites-available/deeptutor`

### 6.2 Upstream 定义

```nginx
# LinChat
upstream linchat_frontend { server 127.0.0.1:3784; keepalive 32; }
upstream linchat_backend  { server 127.0.0.1:8002; keepalive 32; }

# DeepTutor (同机部署的另一个项目)
upstream frontend { server 127.0.0.1:3783; keepalive 32; }
upstream backend  { server 127.0.0.1:8001; keepalive 32; }

# Langfuse
upstream langfuse_web { server 127.0.0.1:3100; keepalive 32; }
```

### 6.3 路由规则

#### 端口 8080 (主入口)

| 路径匹配 | 目标 | 说明 |
|----------|------|------|
| `/linchat/api/*` | linchat_backend (8002) | LinChat 后端 API |
| `/linchat/*` | linchat_frontend (3784) | LinChat 前端页面 |
| `/api/*` | backend (8001) | DeepTutor 后端 API |
| `/*` | frontend (3783) | DeepTutor 前端页面 |

#### 端口 8081 (Langfuse)

| 路径匹配 | 目标 | 说明 |
|----------|------|------|
| `/*` | langfuse_web (3100) | Langfuse 监控平台 |

### 6.4 SSE 与 WebSocket 注意事项

Nginx 需要为 SSE 和 WebSocket 连接配置特殊参数：

- **SSE**: 禁用缓冲 (`proxy_buffering off`)，增大超时
- **WebSocket**: 升级协议头 (`Upgrade` + `Connection`)

### 6.5 管理命令

```bash
sudo nginx -t                  # 测试配置语法
sudo nginx -s reload           # 重载配置
sudo systemctl status nginx    # 查看 Nginx 状态
```

---

## 7. Redis 数据库分配

LinChat 使用 Redis 的 5 个数据库（DB0~DB4），通过数据库编号实现逻辑隔离：

| DB | 用途 | 连接方式 | 说明 |
|----|------|----------|------|
| **DB0** | Django 缓存 + 应用数据 | `django-redis` / `redis.asyncio` | Token、验证码、频率限制、语音会话状态、GPU 锁、推理任务 |
| **DB1** | Langfuse | Langfuse 容器内部 | Langfuse 缓存和队列 |
| **DB2** | Celery Broker + Result | `celery[redis]` | 任务队列和结果存储 |
| **DB3** | Django Channels | `channels_redis` | WebSocket 消息层 (语音交互) |
| **DB4** | LangGraph Checkpoint | `langgraph-redis` | Agent 状态持久化 (24h TTL) |

### 7.1 DB0 键名规范

DB0 承载最多的业务数据，使用前缀隔离不同用途：

| 键名模式 | 用途 | TTL |
|----------|------|-----|
| `auth:token:{hash}` | Token 信息 | 空闲 1h / 绝对 24h |
| `auth:user_token:{uid}` | 用户当前 Token 索引 (SSO) | 同 Token |
| `auth:captcha:{id}` | 验证码文本 | 2 分钟 |
| `auth:fail:{username}` | 登录失败计数 | 15 分钟 |
| `events:user:{uid}` | SSE Pub/Sub 频道 | 无 (Pub/Sub) |
| `voice:session:{uid}` | 语音会话状态 | 120 秒 |
| `voice:active_conv:{uid}` | 活跃对话标记 | 30 秒 |
| `voice:audio_cache:{uid}:*` | 音频帧缓存 | 300 秒 |
| `gpu:lock:*` | GPU 互斥锁 | 按任务 |
| `inference:task:{id}` | 推理任务状态 | 300 秒 |
| `rate_limit:*` | 频率限制计数器 | 按规则 |

### 7.2 连接配置

```python
# DB0 (settings.py)
REDIS_URL = "redis://:redis_linchat_123@localhost:6379/0"
# 连接池: 最大 50 连接，连接/读写超时 5 秒

# DB2 (settings.py)
CELERY_BROKER_URL = "redis://:redis_linchat_123@localhost:6379/2"

# DB3 (settings.py)
CHANNELS_REDIS_URL = "redis://:redis_linchat_123@localhost:6379/3"
```

---

## 8. Celery 定时任务

Celery Beat 负责调度以下定时任务，配置在 `backend/core/celery.py`：

| 任务名 | 任务路径 | 调度周期 | 说明 |
|--------|----------|----------|------|
| `retry-failed-embeddings` | `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的记忆 Embedding 生成 |
| `generate-daily-summary` | `memory.generate_daily_summary` | 每天 00:00 | 基于当天对话生成每日记忆总结 |
| `generate-monthly-summary` | `memory.generate_monthly_summary` | 每月 1 日 00:00 | 生成每月记忆汇总 |
| `embedding-health-check` | `memory.embedding_health_check` | 每小时整点 | Embedding 服务健康检查 |
| `clean-expired-media` | `media.clean_expired_media` | 每日 03:00 | 清理过期媒体文件 (超过 7 天) |
| `retry-failed-doc-embeddings` | `media.retry_failed_doc_embeddings` | 每 5 分钟 | 重试失败的文档 Embedding 生成 |
| `expire-guests` | `users.expire_guests` | 每小时整点 | 清理过期访客账户 |

### 8.1 Celery 配置参数

```python
CELERY_BROKER_URL = "redis://:redis_linchat_123@localhost:6379/2"
CELERY_RESULT_BACKEND = "redis://:redis_linchat_123@localhost:6379/2"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_ENABLE_UTC = False
```

### 8.2 启动命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# Worker (处理任务)
celery -A core worker --loglevel=info

# Beat (调度定时任务)
celery -A core beat --loglevel=info
```

> **建议**: 使用 `./scripts/services.sh start` 统一管理，避免手动启动产生孤儿进程。

---

## 9. LLM Gateway 配置

LinChat 通过 LLM Gateway 统一访问各类 AI 服务（推理、ASR、TTS、文档解析）。Gateway 经由 frpc STCP visitor 在本地 `127.0.0.1:8100` 提供服务。

### 9.1 HTTP 端点

| 端点 | 超时 | 用途 |
|------|------|------|
| `POST /v1/chat/completions` | 180s | LLM 推理请求 |
| `POST /v1/chat/completions/cancel` | 5s | 取消推理 |
| `GET /v1/chat/completions/{id}` | 30s | 轮询推理结果 |
| `POST /v1/documents/parse` | 480s | 创建文档解析任务 |
| `GET /v1/documents/tasks/{id}` | 30s | 查询文档解析结果 |

### 9.2 WebSocket 端点

| 端点 | 用途 | 说明 |
|------|------|------|
| `ws://127.0.0.1:8100/v1/audio/transcriptions/stream` | ASR 流式转录 | 长期存活连接，心跳 30s/60s |
| `ws://127.0.0.1:8100/v1/audio/speech/stream` | TTS 流式合成 | 按请求建立连接 |

### 9.3 超时配置层级

Gateway 超时分为 6 个独立配置项，适应不同场景的耗时特征：

```
LLM_GATEWAY_INFERENCE_TIMEOUT = 180s     # 常规 LLM 推理
LLM_GATEWAY_CANCEL_TIMEOUT = 5s          # 取消请求 (应秒级响应)
LLM_GATEWAY_POLL_TIMEOUT = 30s           # 状态轮询
LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = 480s  # 文档解析 (模型切换可能耗时 6 分钟)
LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT = 30s   # 文档解析结果查询
LLM_GATEWAY_GUARDRAILS_LEVEL = "fast"        # 护栏级别 (< 10ms)
```

### 9.4 模型配置

LLM 模型信息已从环境变量迁移到数据库（`ModelConfig` 表），支持三种类型：

| 类型 | 用途 | 管理方式 |
|------|------|----------|
| `tool` | 主对话 LLM (工具调用) | 后台 API / 设置页面 |
| `multimodal` | 多模态推理 (图片/视频) | 后台 API / 设置页面 |
| `embedding` | 向量 Embedding | 后台 API / 设置页面 |

API Key 使用国密 SM4 加密存储在数据库中。通过 `apps.models.services.model_service.get_active_model()` 获取当前活跃模型配置。

---

## 10. 安全配置

### 10.1 加密算法

| 算法 | 用途 | 配置项 |
|------|------|--------|
| SM3 (国密哈希) | 密码存储 | 无需配置 (固定算法) |
| SM4 (国密对称加密) | API Key 加密存储、密码传输加密 | `SM4_SECRET_KEY` (16 字节) |

SM4 密钥在前后端之间共享，用于：
- 前端加密密码后传输到后端
- 后端加密 API Key 存储到数据库
- 前端加密 API Key 传输到后端

### 10.2 Token 认证

| 特性 | 说明 |
|------|------|
| 存储位置 | httpOnly Cookie (禁止 localStorage) |
| 空闲超时 | 1 小时无操作自动过期 |
| 绝对超时 | 24 小时后强制过期 |
| 单点登录 | 同一用户仅允许一个有效 Token |
| HTTPS | 生产环境强制 Secure Cookie |

### 10.3 频率限制

| 用户类型 | 限制 | 配置位置 |
|----------|------|----------|
| 匿名用户 | 100 次/小时 | DRF `DEFAULT_THROTTLE_RATES` |
| 认证用户 | 1000 次/小时 | DRF `DEFAULT_THROTTLE_RATES` |
| LLM 调用 | 60 次/分钟 | 自定义 Rate Limiter |
| 多模态推理 | 60 秒间隔 | `MULTIMODAL_RATE_LIMIT_SECONDS` |

### 10.4 账户安全

| 机制 | 说明 |
|------|------|
| 验证码 | 登录时必须提交图形验证码，有效期 2 分钟 |
| 失败锁定 | 连续 5 次登录失败后锁定 15 分钟 |
| CORS | 严格限定允许的源地址 |
| XSS 防护 | `SECURE_BROWSER_XSS_FILTER = True` |
| 内容嗅探防护 | `SECURE_CONTENT_TYPE_NOSNIFF = True` |
| 点击劫持防护 | `X_FRAME_OPTIONS = "DENY"` |

### 10.5 CORS 配置

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3784",
    "http://127.0.0.1:3784",
    "http://www.greydan.xin",
]
CORS_ALLOW_CREDENTIALS = True  # 允许携带 Cookie
```

生产环境中，`CORS_ALLOWED_ORIGINS` 必须配置为实际的前端域名地址。

---

## 11. 参考文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 系统架构 | [system-architecture.md](system-architecture.md) | 整体架构设计和组件关系 |
| 部署指南 | [deployment-guide.md](deployment-guide.md) | 部署流程和运维操作 |
| 项目宪法 | [../.specify/memory/constitution.md](../.specify/memory/constitution.md) | 不可违背的架构原则和安全约束 |
| 代码示例 | [constitution-examples.md](constitution-examples.md) | 编码规范和示例代码 |
| Gateway 集成 | [linchat-integration-guide.md](linchat-integration-guide.md) | LLM Gateway 接口集成指南 |
| Langfuse 集成 | [langfuse-trace-peek.md](langfuse-trace-peek.md) | Langfuse Trace 查看指南 |
| TTS WebSocket | [tts-websocket-api.md](tts-websocket-api.md) | TTS 流式合成 WebSocket API |
| 多模态 API | [multimodal-api-guide.md](multimodal-api-guide.md) | 多模态推理 API 指南 |
| 测试指南 | [testing-guide.md](testing-guide.md) | 测试编写和运行指南 |
