# Backend 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

## 项目结构

```
backend/
├── core/                  # Django 项目配置（settings, urls, asgi, celery, redis）
├── apps/
│   ├── agent/             # Prompt 模板目录（fallback_router.j2 拒识兜底）
│   ├── chat/              # 聊天核心（消息收发、SSE 流式、生成控制）
│   ├── common/            # 通用工具（中间件、WebSocket 认证、异常、SSE、Gateway、tokenizer、storage/）
│   ├── context/           # Prompt 构建与上下文裁剪（23 个 Jinja2 模板）、Token 预算管理、监控 API
│   ├── graph/             # LangGraph Agent（Agent 工厂、6 个 SubAgent、工具链、推理取消、services/helpers/ 拆分）
│   ├── media/             # 媒体附件（上传/下载、文档解析+RAG 向量分块+缓存、过期清理）
│   ├── memory/            # 用户记忆（CRUD、向量搜索、Embedding、定时总结）
│   ├── models/            # LLM 模型配置（tool/multimodal/embedding CRUD、SM4 加密密钥）
│   ├── users/             # 用户认证（验证码、登录/登出、Token、SSO、成员管理 member_service）
│   └── voice/             # 语音交互（WebSocket 流、ASR→Agent→TTS 管道、声纹、设备、ambient 监听、设备独占）
├── tests/                 # 81 个测试文件，1445+ 测试函数，按模块组织
├── scripts/               # 工具脚本（init_minio.py, benchmark_models.py, voice_latency_test.py, test_qwen.py）
├── conftest.py            # pytest 全局配置（禁用限流）
├── pytest.ini             # pytest 配置（--reuse-db）
└── requirements.txt       # Python 依赖（66 个包）
```

## App 职责

| App | 关键模型 | 说明 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式响应、生成控制（停止/恢复/重连）、历史消息排除限流 |
| `common` | 无 | Token 中间件、WebSocket 认证、异常体系、响应格式、SSE 事件、Gateway 调用（Langfuse 单例）、Rate Limiter、MinIO 存储封装、异步任务工具（async_utils） |
| `context` | 无 | Prompt 构建（PromptBuilder + builder_helpers）、上下文裁剪（Trimmer）、Token 预算、监控 API、23 个 Jinja2 模板 |
| `graph` | 无 | LangGraph Agent 创建/执行、6 个 SubAgent（搜索/记忆/代码/HA/多模态/文档）、多模态直连推理、推理取消、GPU 锁 |
| `media` | MediaAttachment, DocumentChunkEmbedding | 媒体上传/下载、文档解析（Gateway）+ RAG 向量分块（1024 维 pgvector）、双层缓存、过期清理、音频工具 |
| `memory` | UserMemory, UserMemoryEmbedding | 用户记忆 CRUD、pgvector 混合搜索（0.7 向量 + 0.3 关键词）、Embedding、每日/每月总结 |
| `models` | ModelConfig | LLM 模型配置（tool/multimodal/embedding）CRUD、SM4 加密密钥、活跃模型查询 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权（httpOnly Cookie）、SSO、SM3/SM4、家庭成员管理、访客过期 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | WebSocket 语音流 → ASR 流式转录 → Agent Pipeline（纯口语 Prompt） → TTS 流式合成、声纹、设备、ambient 环境监听（VAD 不触发 active_conv + ASR 自动重连）、设备独占 |

## SubAgent 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent      → web_search + mem_search                          (60s 超时)
  ├── memory_subagent      → mem_search/cache/update/delete + web_search      (60s 超时)
  ├── code_subagent        → python_exec + mem_search + web_search            (60s 超时)
  ├── ha_subagent          → ha_query/control/diagnose + mem_search + web_search (60s 超时)
  ├── multimodal_subagent  → multimodal_analyze + mem_search + web_search     (1200s 超时)
  ├── document_subagent    → document_parse + doc_rag_search + mem_search     (1200s 超时)
  └── history_search       → 直接工具（非 SubAgent）
```

## 语音管道架构

```
ESP 设备/浏览器 (PCM 音频) → WebSocket → VoiceConsumer (3 Mixin 架构)
  → ASRStreamClient(BaseWSClient) → Gateway ASR (长期存活, 心跳 30s/60s)
  → [voice_chat] transcription → VoicePipeline → Agent → TTSPipelineManager → 前端播放
  → [ambient]    transcription → UtteranceAggregator (3s 聚合) → ResponseDecisionService
                                  → RESPOND: VoicePipeline → TTSRouter (group_send) → 浏览器/HA 音箱播放
                                  → RECORD_ONLY: voice_persist_service.record_only_ambient()（上限 20 条自动清理）
                                  → STOP: 取消管道 + 重置聚合器
  设备独占: ambient 连接注册 Redis 键 voice:ambient_conn:{uid}，设备连接优先于浏览器
```

## 数据模型总览（11 个）

| 模型 | App | 表名 | 关键字段 |
|------|-----|------|----------|
| Message | chat | `message` | user_id, role, content, status(0-3), sequence, is_voice, speaker_id |
| LangGraphExecution | chat | `langgraph_execution` | request_id, status, duration_ms, langfuse_trace_id |
| MediaAttachment | media | `media_attachment` | media_type, storage_path, embedding_status, parsed_content |
| DocumentChunkEmbedding | media | `document_chunk_embedding` | chunk_text, embedding(1024 dims) |
| UserMemory | memory | `user_memory` | type(memory/compaction/daily/monthly), embedding_status |
| UserMemoryEmbedding | memory | `user_memory_embedding` | embedding(1024 dims), chunk_text |
| ModelConfig | models | `model` | type(tool/multimodal/embedding), api_key(SM4) |
| SysUser | users | `sys_user` | type(admin/user), member_type(member/guest), password_hash(SM3) |
| SpeakerProfile | voice | `voice_speaker_profile` | OneToOne→SysUser, gateway_speaker_id |
| RegisteredDevice | voice | `voice_registered_device` | device_uuid, api_token_encrypted(SM4) |
| VoiceSettings | voice | `voice_settings` | wake_words, tts_output_device(browser/ha_speaker) |

## core 模块

| 文件 | 职责 |
|------|------|
| `settings.py` (512 行) | 全局配置：数据库、Redis(4 DB)、LLM 超时/重试、Gateway、MinIO（含 audio 桶）、媒体限制、语音（active_conv 10s、LLM 阈值 0.75、HA_LAN_HOST）、认证、Memory、安全 |
| `urls.py` | 顶层路由分发（`api/v1/` 前缀） |
| `asgi.py` | ASGI 入口：ProtocolTypeRouter（HTTP + WebSocket 语音） |
| `celery.py` | Celery 应用 + Beat 定时任务（5 个） |
| `redis.py` | 异步/同步 Redis 客户端 + 键名工具 + Pub/Sub 频道 |

### Redis 分配

| DB | 用途 |
|----|------|
| DB0 | django-redis 缓存 + 应用数据（Token/Session/限流） |
| DB1 | Langfuse（外部） |
| DB2 | Celery Broker/Result |
| DB3 | Django Channels (WebSocket) |

### Celery 定时任务

| 任务 | 调度 | 说明 |
|------|------|------|
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆总结 |
| `memory.generate_monthly_summary` | 每月 1 日 | 每月记忆总结 |
| `memory.embedding_health_check` | 每小时 | Embedding 健康检查 |
| `media.clean_expired_media` | 每天 03:00 | 清理过期媒体文件 |

### 路由分发

| 路径前缀 | 目标模块 |
|----------|---------|
| `api/v1/auth/` | `apps.users.urls` |
| `api/v1/members/` | `apps.users.member_urls` |
| `api/v1/chat/media/` | `apps.media.urls` |
| `api/v1/chat/documents/` | `apps.media.document_urls` |
| `api/v1/chat/inference/` | `apps.graph.urls` |
| `api/v1/chat/` | `apps.chat.urls` |
| `api/v1/models/` | `apps.models.urls` |
| `api/v1/memories/` | `apps.memory.urls` |
| `api/v1/voice/` | `apps.voice.urls` |
| `api/v1/events` | `apps.common.urls` |
| `ws/voice/` | `apps.voice.routing` |

## 关键依赖

| 类别 | 技术 |
|------|------|
| Web 框架 | Django 4.2+ / DRF 3.14+ / uvicorn 0.30+ |
| AI Agent | LangGraph 0.2+ / LangChain 0.2+ / langfuse 4.0+ |
| 任务队列 | Celery 5.3+ (Redis DB2) |
| 数据库 | PostgreSQL 15 + pgvector (1024 维向量) |
| 缓存 | Redis DB0 (django-redis) / DB3 (Channels) |
| 对象存储 | MinIO (minio SDK) |
| WebSocket | Django Channels 4.0+ (Redis DB3) |
| HTTP 客户端 | httpx (异步 Gateway 调用) |
| Gateway WS | websockets 12.0+ (ASR 流式转录 + TTS 流式合成) |
| 国密算法 | gmssl (SM3 哈希 + SM4 加密) |
| Token 计数 | tiktoken (cl100k_base 编码, 单例) |
| 拼音匹配 | pypinyin (唤醒词模糊匹配) |
| 模板引擎 | Jinja2 3.1+ (Prompt 模板) |
| 重试策略 | tenacity 8.0+ (Gateway 指数退避) |

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全量测试（81 个文件，1445+ 测试函数）
pytest

# 按模块测试
pytest tests/chat/ -v               # 聊天模块
pytest tests/voice/ -v              # 语音模块（18 个文件）
pytest tests/apps/graph/ -v         # Agent 模块
pytest tests/media/ -v              # 媒体/文档模块
pytest tests/memory/ -v             # 记忆模块
pytest tests/users/ -v              # 用户模块
pytest tests/models/ -v             # 模型配置
pytest tests/common/ -v             # 公共工具
pytest tests/context/ -v            # 上下文监控
pytest tests/integration/ -v        # 集成测试
pytest tests/performance/ -v        # 性能测试

# 覆盖率
pytest --cov=apps --cov-report=term-missing
```

### 测试目录结构

| 目录 | 文件数 | 覆盖模块 |
|------|--------|---------|
| `tests/chat/` | 17 | chat + 部分 media/graph 兼容层 |
| `tests/voice/` | 18 | voice 全模块（consumer/pipeline/decision/TTS/ASR/session） |
| `tests/memory/` | 8 | memory 全模块（models/repos/services/tasks/tools/isolation） |
| `tests/users/` | 9 | users 全模块（auth/member/middleware/commands） |
| `tests/models/` | 6 | models 全模块（CRUD/serializers/integration） |
| `tests/apps/graph/` | 6 | graph SubAgent（document/ha/subagent_autonomy） |
| `tests/media/` | 4 | media 服务（document_cache/chunk/rag/parse） |
| `tests/common/` | 2 | event_service/tokenizer |
| `tests/context/` | 1 | monitoring |
| `tests/integration/` | 1 | SSE 异步集成 |
| `tests/performance/` | 2 | smoke + SSE load |

## 常用命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 启动后端（必须 uvicorn，生产环境加 PYTHONUNBUFFERED=1 避免日志缓冲丢失 traceback）
PYTHONUNBUFFERED=1 uvicorn core.asgi:application --host 0.0.0.0 --port 8002

# Celery
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# 数据库
python manage.py migrate
python manage.py makemigrations

# 管理命令
python manage.py init_admin
python manage.py reset_all_data --password <密码> --audio <音频路径> --yes
```

## scripts 工具脚本

| 脚本 | 说明 |
|------|------|
| `init_minio.py` | 初始化 MinIO 存储桶 |
| `benchmark_models.py` | LLM 模型基准测试（4 模型对比） |
| `voice_latency_test.py` | 语音延迟测试 |
| `test_qwen.py` | Qwen 模型测试 |

## 架构约束

1. **三层架构**: views -> services -> repositories，禁止跨层
2. **用户隔离**: 所有操作按 `user_id` 粒度，不存在会话粒度
3. **ASGI 必须**: 禁止 `runserver`，必须 `uvicorn`
4. **统一响应**: `{"code": "...", "message": "...", "data": ...}`
5. **Langfuse 4.x**: 使用 `start_observation()` API，客户端模块级单例，不同步 flush
6. **PYTHONUNBUFFERED=1**: 后端 nohup 启动必须设置，否则日志缓冲导致 traceback 丢失
7. **GPU 互斥**: Embedding 和语言模型共享 GPU 时通过 task_helpers 协调，避免 OOM


<claude-mem-context>
# Recent Activity

### Feb 12, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #995 | 4:25 PM | 🔵 | Backend Environment Configuration Review | ~375 |

### Mar 11, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1625 | 8:32 AM | 🔵 | Current LLM Configuration in LinChat Backend | ~260 |
</claude-mem-context>