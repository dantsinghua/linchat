# LinChat — 代码库摘要

> 代码库结构、模块职责、关键文件与依赖清单
> 最后更新: 2026-03-31

---

## 代码库概述

| 指标 | 数值 |
|------|------|
| 后端代码量 | ~13,000 行（304 个 Python 文件，`apps/` 目录） |
| 前端代码量 | ~13,000 行（206 个 TS/TSX 文件） |
| 测试代码 | 89 个后端测试文件 + 35 个前端测试文件，1,462+ 测试函数 |
| 主要语言 | Python 3.11+（后端）、TypeScript 5.0+（前端） |
| 后端框架 | Django 4.2+ / DRF 3.14+ / uvicorn 0.30+ (ASGI) |
| 前端框架 | Next.js 14+ / React 18+ / Zustand |
| AI Agent | LangGraph + LangChain + Langfuse |
| 数据库 | PostgreSQL 15 (pgvector) + Redis + MinIO |
| Django 模型 | 11 个 |
| 服务类 | 20+ 个 |
| 已完成特性 | 15 个（001 ~ 015） |

---

## 项目结构

```
linchat/
├── backend/                   # Django 后端 (~304 .py, ~13k LOC)
│   ├── core/                  # 项目配置 (settings, urls, asgi, celery, redis)
│   ├── apps/                  # 10 个 Django App
│   │   ├── chat/              # 消息 CRUD、SSE 流式、推理取消
│   │   ├── common/            # 中间件、异常、SSE 事件、存储、工具
│   │   ├── context/           # Prompt 构建、Token 预算、Jinja2 模板 (23个)
│   │   ├── graph/             # LangGraph Agent、6 个 SubAgent、工具
│   │   ├── media/             # 媒体上传/下载、文档解析 + RAG
│   │   ├── memory/            # 用户记忆 CRUD、pgvector 搜索、Embedding
│   │   ├── models/            # LLM 模型配置、SM4 加密
│   │   ├── users/             # 认证、SSO、成员管理、SM3/SM4
│   │   ├── voice/             # WebSocket 语音流、ASR/TTS 管道
│   │   └── agent/             # Prompt 模板 (占位)
│   ├── tests/                 # 89 个测试文件, 1462+ 测试函数
│   └── scripts/               # 工具脚本 (benchmark, voice_test)
├── frontend/                  # Next.js 前端 (~206 .ts/.tsx, ~13k LOC)
│   └── src/
│       ├── app/               # 5 个页面 (chat, settings, login, 401, home)
│       ├── components/        # 30+ React 组件
│       ├── hooks/             # 8 个自定义 Hook
│       ├── stores/            # 5 个 Zustand Store
│       ├── services/          # 8 个 API 服务
│       ├── types/             # 5 个类型定义
│       └── utils/             # SM4 加密工具
├── docs/                      # 项目文档 (16+ .md)
├── specs/                     # 15 个特性规范
├── scripts/                   # 服务管理脚本 (services.sh)
├── docker/                    # Docker 构建文件
└── docker-compose.yml         # 9 个 Docker 服务
```

---

## 后端模块详解

### core/ — 项目配置

| 文件 | LOC | 职责 |
|------|-----|------|
| `settings.py` | 507 | 全量配置：DB、Redis、LLM Gateway、Celery、Langfuse、语音、媒体 |
| `celery.py` | — | Celery 应用 + Beat 定时任务调度 |
| `redis.py` | 195 | 同步/异步 Redis 客户端封装、Pub/Sub、分布式锁 |
| `asgi.py` | — | ASGI 入口（uvicorn + Channels routing） |

### chat/ — 聊天核心

| 文件 | 职责 |
|------|------|
| `services/chat_service.py` | 消息发送：验证→附件加载→Agent 执行 |
| `services/history_service.py` | 消息检索：游标分页、附件预取 |
| `views.py` (222) | 6 个端点：chat, messages, generating, stop, resume, reconnect |
| `models.py` | Message (11 字段), LangGraphExecution (14 字段) |

### graph/ — Agent 引擎

| 文件 | 职责 |
|------|------|
| `services/agent_service.py` (246) | Agent 执行：LLM + SubAgent 编排、StreamChunk、Token 统计 |
| `services/context_service.py` (149) | Token 预算：有效窗口 90%，3 级压缩 (历史/工具/记忆) |
| `subagents/` (6 文件) | search, memory, code, HA, multimodal, document |
| `tools/` (6 文件) | web_search, mem_*, python_exec, ha_*, doc_* |
| `graph.py` | `create_chat_agent()` 工厂 |

### voice/ — 语音交互

| 文件 | 职责 |
|------|------|
| `consumers.py` | WebSocket VoiceConsumer (3 Mixin) |
| `consumer_session.py` (184) | 会话状态：ASR 连接、音频帧缓冲、超时 |
| `services/tts_pipeline_manager.py` (148) | TTS 编排：安慰语音、队列、barge-in |
| `services/response_decision_service.py` (147) | 8 级决策链 + LLM 意图分类 |
| `services/speaker_service.py` (162) | 声纹注册/删除 |

### media/ — 媒体与文档

| 文件 | 职责 |
|------|------|
| `services/document.py` (216) | Gateway 文档解析：创建/轮询/结果获取 |
| `services/document_rag.py` (161) | 语义分块 + pgvector 存储 (1024 dims) |
| `repositories.py` (201) | 混合搜索 (0.7 向量 + 0.3 关键词) |

### memory/ — 用户记忆

| 文件 | 职责 |
|------|------|
| `services.py` (149) | 记忆 CRUD + Embedding 异步派发 |
| `repositories.py` (148) | pgvector CosineDistance + pg_jieba 全文检索 |

### users/ — 认证与成员

| 文件 | 职责 |
|------|------|
| `views.py` (222) | 验证码、登录、登出、Token 认证、成员 CRUD |
| `models.py` (149) | SysUser：SM4 加密、双过期 Token、锁定 |
| `member_service.py` (149) | 家庭账户 (015)：多用户上下文切换 |

---

## 前端模块详解

### Hooks（核心逻辑）

| Hook | LOC | 职责 |
|------|-----|------|
| `useVoiceMode` | 515 | 8 状态 FSM：idle→configuring→listening→recording→processing→responding |
| `useVoiceWebSocket` | 423 | WebSocket：16 事件、30s 心跳、自动重连 |
| `useChatStream` | 396 | 聊天核心：send, stop, resume, retry, 历史分页 |
| `usePCMAudioCapture` | 306 | AudioWorklet PCM16：16kHz 单声道、30ms 帧 |
| `useAuth` | 255 | 认证状态 + SSE 事件分发 |

### Stores（Zustand 状态）

| Store | 职责 |
|-------|------|
| `chatStore` | 消息列表、生成状态、错误、上下文监控 |
| `voiceStore` | 语音模式、会话、设置 |
| `uploadStore` | 媒体上传任务队列 |
| `modelStore` | 模型配置 |
| `memberStore` | 用户切换 (015 多用户) |

### Components（30+ 组件）

| 类别 | 数量 | 关键组件 |
|------|------|----------|
| Chat | 11 | MessageList, MessageInput, ContextMonitorPanel, MediaUploader |
| Voice | 4 | VoiceModePanel, VoiceWaveform, VoiceMessageBubble |
| Settings | 5 | ModelConfigForm, SpeakerProfileCard, VoiceSettingsCard |
| Members | 4 | CreateMemberWizard, MemberSwitchModal, VoiceprintRecorder |
| Auth | 3 | LoginForm, CaptchaImage |

---

## 数据模型 (11 个)

| 模型 | App | 关键字段 |
|------|-----|----------|
| `Message` | chat | user_id, role, content, status (0-3), tokens, is_voice |
| `LangGraphExecution` | chat | request_id, graph_name, status, duration_ms, langfuse_trace_id |
| `MediaAttachment` | media | file_type, s3_path, mime_type, duration_sec, expires_at |
| `DocumentChunkEmbedding` | media | chunk_text, embedding (1024 dims) |
| `UserMemory` | memory | content, type, embedding_status |
| `UserMemoryEmbedding` | memory | embedding (1024 dims) |
| `ModelConfig` | models | model_type, model_name, api_key (SM4) |
| `SysUser` | users | password_hash (SM3), api_token, token_expiry |
| `SpeakerProfile` | voice | gateway_speaker_id, quality_score |
| `RegisteredDevice` | voice | api_token_encrypted (SM4) |
| `VoiceSettings` | voice | wake_words, recording_mode, vad_sensitivity |

---

## 关键依赖

### 后端 (Top 15)

| 包 | 版本 | 用途 | 类型 |
|---|------|------|------|
| Django | >=4.2,<5.0 | Web 框架 | runtime |
| djangorestframework | >=3.14.0 | REST API | runtime |
| langchain | >=0.2.0 | LLM 框架 | runtime |
| langgraph | >=0.2.0 | Agent 编排 | runtime |
| langfuse | >=3.12.0,<4.0.0 | 可观测性 | runtime |
| celery | >=5.3.0 | 任务队列 | runtime |
| psycopg2-binary | >=2.9.9 | PostgreSQL | runtime |
| redis | >=5.0.0 | Redis 客户端 | runtime |
| pgvector | >=0.3.0 | 向量 DB | runtime |
| gmssl | >=3.2.2 | 国密 SM3/SM4 | runtime |
| channels | >=4.0 | WebSocket | runtime |
| websockets | >=12.0 | WS 客户端 | runtime |
| httpx | >=0.27.0 | 异步 HTTP | runtime |
| tiktoken | >=0.7.0 | Token 计数 | runtime |
| minio | >=7.2.0 | S3 客户端 | runtime |

### 前端 (Top 15)

| 包 | 版本 | 用途 | 类型 |
|---|------|------|------|
| next | ^14.2.0 | React 框架 | runtime |
| react | ^18.3.0 | UI | runtime |
| zustand | ^4.5.0 | 状态管理 | runtime |
| axios | ^1.6.0 | HTTP | runtime |
| tailwindcss | ^3.4.0 | CSS | dev |
| react-markdown | ^9.0.0 | Markdown | runtime |
| mermaid | ^10.9.0 | 图表 | runtime |
| sm-crypto | ^0.3.13 | SM4 加密 | runtime |
| sonner | ^2.0.7 | Toast | runtime |
| typescript | ^5.4.0 | 类型 | dev |
| jest | ^29.7.0 | 测试 | dev |
| @playwright/test | ^1.42.0 | E2E | dev |
| eslint | ^8.57.0 | Lint | dev |
| prettier | ^3.2.0 | 格式化 | dev |
| rehype-highlight | ^7.0.0 | 高亮 | runtime |

---

## Celery 定时任务

| 任务 | 频率 | 职责 |
|------|------|------|
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆摘要 |
| `memory.generate_monthly_summary` | 每月 1 日 | 月度记忆摘要 |
| `memory.embedding_health_check` | 每小时 | Embedding 健康检查 |
| `media.clean_expired_media` | 每天 03:00 | 清理过期媒体 |

---

*本文档由 autoresearch:learn 自动生成。*
