# Backend 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

## 项目结构

```
backend/
├── core/                  # Django 项目配置（settings, urls, asgi, celery, redis）
├── apps/
│   ├── chat/              # 聊天核心（消息收发、SSE 流式、推理取消）
│   ├── common/            # 通用工具（中间件、异常、响应格式、SSE、Gateway、tokenizer、storage/）
│   ├── context/           # Prompt 构建与上下文裁剪（16 个 Jinja2 模板）、Token 预算管理、监控 API
│   ├── graph/             # LangGraph Agent（Agent 工厂、6 个 SubAgent、工具链、推理取消）
│   ├── media/             # 媒体附件（上传/下载、文档解析+RAG 向量分块、过期清理）
│   ├── memory/            # 用户记忆（CRUD、向量搜索、Embedding、定时总结）
│   ├── models/            # LLM 模型配置（tool/multimodal/embedding CRUD、SM4 加密密钥）
│   ├── users/             # 用户认证（验证码、登录/登出、Token、SSO）
│   └── voice/             # 语音交互（WebSocket 流、ASR→Agent→TTS 管道、声纹、设备、ambient 监听）
├── tests/                 # 68 个测试文件，按模块组织: chat/ common/ context/ apps/graph/ media/ memory/ models/ users/ voice/ integration/ performance/
├── scripts/               # 工具脚本（init_minio.py）
├── conftest.py            # pytest 全局配置（禁用限流）
├── pytest.ini             # pytest 配置（--reuse-db）
└── requirements.txt       # Python 依赖（65+ 个包）
```

## App 职责

| App | 关键模型 | 说明 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式响应、推理取消 |
| `common` | 无 | Token 中间件、异常体系、响应格式、SSE 事件、Gateway 调用（Langfuse 单例）、Rate Limiter、MinIO 存储封装、异步任务工具（async_utils） |
| `context` | 无 | Prompt 构建（PromptBuilder + builder_helpers）、上下文裁剪（Trimmer）、Token 预算、监控 API、16 个 Jinja2 模板 |
| `graph` | 无 | LangGraph Agent 创建/执行、6 个 SubAgent（搜索/记忆/代码/HA/多模态/文档）、推理取消、GPU 锁 |
| `media` | MediaAttachment, DocumentChunkEmbedding | 媒体上传/下载、文档解析（Gateway）+ RAG 向量分块（1024 维 pgvector）、过期清理任务 |
| `memory` | UserMemory, UserMemoryEmbedding | 用户记忆 CRUD、pgvector 向量搜索（混合搜索 0.7 向量 + 0.3 关键词）、Embedding、每日/每月总结 |
| `models` | ModelConfig | LLM 模型配置（tool/multimodal/embedding）CRUD、SM4 加密密钥、活跃模型查询 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权（httpOnly Cookie）、SSO、SM3/SM4、账户锁定 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹、设备、ambient 环境监听（014） |

## SubAgent 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent      → web_search + mem_search                          (60s 超时)
  ├── memory_subagent      → mem_search/cache/update/delete + web_search      (60s 超时)
  ├── code_subagent        → python_exec + mem_search + web_search            (60s 超时)
  ├── ha_subagent          → ha_query/control/diagnose + mem_search           (60s 超时)
  ├── multimodal_subagent  → multimodal_analyze + mem_search + web_search     (1200s 超时)
  ├── document_subagent    → document_parse + doc_rag_search + mem_search     (1200s 超时, 011 新增)
  └── history_search       → 直接工具（非 SubAgent）
```

## 语音管道架构

```
ESP 设备/浏览器 (PCM 音频) → WebSocket → VoiceConsumer (3 Mixin 架构)
  → ASRStreamClient(BaseWSClient) → Gateway ASR (长期存活, 心跳 30s/60s)
  → [voice_chat] transcription → VoicePipeline → Agent → TTSPipelineManager → 前端播放
  → [ambient]    transcription → UtteranceAggregator (3s 聚合) → ResponseDecisionService
                                  → RESPOND: VoicePipeline → TTSRouter (group_send) → 浏览器播放
                                  → RECORD_ONLY: voice_persist_service.record_only_ambient()（上限 20 条自动清理）
                                  → STOP: 取消管道 + 重置聚合器
```

## 关键依赖

| 类别 | 技术 |
|------|------|
| Web 框架 | Django 4.2+ / DRF 3.14+ / uvicorn 0.30+ |
| AI Agent | LangGraph 0.2+ / LangChain 0.2+ / langfuse 3.12+ |
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

## 常用命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 启动后端（必须 uvicorn，生产环境加 PYTHONUNBUFFERED=1 避免日志缓冲丢失 traceback）
PYTHONUNBUFFERED=1 uvicorn core.asgi:application --host 0.0.0.0 --port 8002

# Celery
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# 测试
pytest                              # 全部（68 个测试文件）
pytest tests/chat/ -v               # 聊天模块
pytest tests/voice/ -v              # 语音模块
pytest tests/apps/graph/ -v         # Agent 模块
pytest tests/media/ -v              # 媒体/文档模块
pytest --cov=apps --cov-report=term-missing  # 覆盖率
```

## 架构约束

1. **三层架构**: views -> services -> repositories，禁止跨层
2. **用户隔离**: 所有操作按 `user_id` 粒度，不存在会话粒度
3. **ASGI 必须**: 禁止 `runserver`，必须 `uvicorn`
4. **统一响应**: `{"code": "...", "message": "...", "data": ...}`
5. **Langfuse 3.x**: 使用 `start_span()` API，客户端模块级单例，不同步 flush
6. **PYTHONUNBUFFERED=1**: 后端 nohup 启动必须设置，否则日志缓冲导致 traceback 丢失


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