# LinChat 项目概览

> 大模型聊天平台 — 项目设计评审文档 (PDR)
>
> **版本**: 2.0.0
> **日期**: 2026-03-31
> **公网地址**: https://www.greydan.xin/linchat

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心功能](#2-核心功能)
3. [技术栈总览](#3-技术栈总览)
4. [系统架构](#4-系统架构)
5. [目标用户与使用场景](#5-目标用户与使用场景)
6. [项目里程碑](#6-项目里程碑)
7. [关键设计决策](#7-关键设计决策)
8. [快速上手](#8-快速上手)

---

## 1. 项目概述

LinChat 是一个面向家庭场景的企业级 AI 聊天平台。平台以 LangGraph 多 Agent 架构为核心，集成了文本对话、语音交互、智能家居控制、文档解析、多模态理解等能力，为家庭成员提供统一的 AI 助手入口。

### 核心定位

| 维度 | 描述 |
|------|------|
| **产品形态** | 家庭 AI 助手（Web + 语音设备） |
| **用户规模** | 家庭级多用户（成员 + 访客机制） |
| **AI 能力** | 6 个专业 SubAgent 协同工作 |
| **交互方式** | 文本聊天 + SSE 流式 + WebSocket 语音 + 环境监听 |
| **部署模式** | 自托管单机部署，frpc + wstunnel 穿透公网 |

### 项目规模

- **后端**: 11 个 Django App，68 个测试文件，1278 个测试用例
- **前端**: Next.js 14 App Router，8 个自定义 Hook，5 个 Zustand Store
- **规范**: 15 个 Speckit 特性规范，全部已完成
- **基础设施**: 6 个 Docker 容器（PostgreSQL, Redis, ClickHouse, MinIO, Langfuse Web/Worker）

---

## 2. 核心功能

### 2.1 文本聊天

SSE 流式响应的 AI 对话系统。支持 Markdown 渲染（GFM + Mermaid 图表）、消息历史加载、乐观更新、断线重连、推理取消。前端通过 `useChatStream` Hook 管理完整的聊天状态机。

### 2.2 多 Agent 工具链

基于 LangGraph 的主 Agent 自动路由到 6 个专业 SubAgent：

| SubAgent | 工具集 | 超时 | 职责 |
|----------|--------|------|------|
| **search** | web_search, mem_search | 60s | 联网搜索 + 记忆检索 |
| **memory** | mem_search/cache/update/delete, web_search | 60s | 用户记忆管理（CRUD） |
| **code** | python_exec, mem_search, web_search | 60s | 代码执行与调试 |
| **ha** | ha_query/control/diagnose, mem_search | 60s | Home Assistant 智能家居 |
| **multimodal** | multimodal_analyze, mem_search, web_search | 1200s | 图片/视频/音频理解 |
| **document** | document_parse, doc_rag_search, mem_search | 1200s | 文档解析 + 向量检索 |

另有 `history_search` 作为直接工具（非 SubAgent）供主 Agent 使用。

### 2.3 上下文记忆系统

基于 pgvector 的混合检索记忆系统：

- **向量搜索**: 1024 维 Embedding，权重 0.7
- **关键词搜索**: 权重 0.3
- **记忆类型**: 用户主动存储 + AI 自动提取 + 每日/每月定时总结（Celery Beat）
- **Prompt 构建**: PromptBuilder + 16 个 Jinja2 模板，Token 预算管理 + 上下文裁剪（Trimmer）

### 2.4 语音交互

全双工语音对话管道，支持两种模式：

- **Voice Chat 模式**: 用户按键录音 → ASR 转写 → Agent 推理 → TTS 合成 → 播放
- **Ambient 模式 (Jarvis)**: 环境持续监听 → 话语聚合（3s 窗口）→ 响应决策（RESPOND / RECORD_ONLY / STOP）→ 主动回应

技术链路：
```
ESP 设备/浏览器 → WebSocket → VoiceConsumer (3 Mixin)
  → ASRStreamClient → Gateway ASR (长期存活, 心跳)
  → VoicePipeline → Agent → TTSPipelineManager → 前端播放
```

语音状态机：8 态（idle → configuring → listening → recording → processing → responding → interrupted → error）。

### 2.5 多模态理解

支持图片、视频、音频、文档四类媒体的上传与 AI 分析。通过 Gateway 调用 MiniCPM-o 模型处理视觉/听觉理解任务。附件限制：图片 10MB / 视频 50MB / 音频 10MB / 时长 60s / 单次附件数 5。

### 2.6 文档 RAG

文档解析 + 向量分块检索：

1. 用户上传 PDF → Gateway 解析为 Markdown
2. 结果按 1024 维向量分块存储（DocumentChunkEmbedding）
3. 后续对话通过 `doc_rag_search` 工具进行语义检索
4. SSE 实时推送解析进度

### 2.7 智能家居控制

通过 Home Assistant SubAgent 实现设备查询、控制、诊断：

- 查询设备状态（灯光、传感器、空调等）
- 执行设备控制（开关、亮度、温度调节）
- 故障诊断与建议

### 2.8 实时监控面板

前端 ContextMonitorPanel 展示当前对话的实时指标：

- Token 消耗（输入/输出）
- 工具调用链路与耗时
- Langfuse Trace 关联

### 2.9 家庭多用户系统

- **成员类型**: 成员（永久）+ 访客（可设过期时间）
- **视角切换**: 成员可切换到其他用户视角查看/代发消息（不换登录态）
- **声纹注册**: 每个用户可注册声纹，语音场景自动识别说话人
- **权限隔离**: 访客无管理入口，无法切换用户

---

## 3. 技术栈总览

### 3.1 后端

| 类别 | 技术 | 版本/说明 |
|------|------|-----------|
| Web 框架 | Django + DRF | 4.2+ / 3.14+ |
| ASGI 服务器 | uvicorn | 0.30+（必须，不使用 runserver） |
| AI Agent | LangGraph + LangChain | 0.2+ |
| 任务队列 | Celery | 5.3+（Redis DB2 作为 Broker） |
| WebSocket | Django Channels | 4.0+（Redis DB3） |
| Gateway WS | websockets | 12.0+（ASR/TTS 流式） |
| HTTP 客户端 | httpx | 异步 Gateway 调用 |
| 国密算法 | gmssl | SM3 哈希 + SM4 加密 |
| Token 计数 | tiktoken | cl100k_base 编码 |
| 监控 | Langfuse | 3.12+（start_span API） |

### 3.2 前端

| 类别 | 技术 | 版本/说明 |
|------|------|-----------|
| 框架 | Next.js + React + TypeScript | 14+ / 18+ / 5.0+ |
| 样式 | Tailwind CSS | — |
| 状态管理 | Zustand | 4.5+（5 个 Store） |
| HTTP | Axios | Cookie 认证 + 401/429 拦截 |
| 加密 | sm-crypto | SM4 前端加密 |
| 音频 | Web AudioWorklet API | PCM16 16kHz 采集 |
| Markdown | react-markdown + Mermaid | GFM + 图表 |
| 通信 | SSE + WebSocket | 聊天流式 + 语音双向 |
| 测试 | Jest + Playwright | 单元 + E2E |

### 3.3 数据层

| 服务 | 用途 | 端口 |
|------|------|------|
| PostgreSQL 15 | 主数据库（pgvector + pg_jieba 扩展） | 5432 |
| Redis | 缓存/会话/Channels/Celery Broker | 6379 |
| ClickHouse | Langfuse 分析数据库 | 8123/9000 |
| MinIO | 对象存储（音频/文档/媒体文件） | 9010/9011 |

**Redis 数据库分配**:

| DB | 用途 |
|----|------|
| DB0 | LinChat 缓存（django-redis，Token 信息） |
| DB1 | Langfuse 缓存 |
| DB2 | Celery Broker |
| DB3 | Django Channels（WebSocket 分组） |

### 3.4 监控

| 服务 | 用途 |
|------|------|
| Langfuse v3 | LLM 调用追踪、Agent 执行链路、Token 消耗统计 |
| ContextMonitorPanel | 前端实时监控面板（内嵌于聊天页面） |

---

## 4. 系统架构

### 4.1 分层架构（强制约束）

```
视图层 (views.py)        → 仅处理 HTTP 请求响应，禁止业务逻辑
服务层 (services/)       → 封装所有业务逻辑（核心层）
数据层 (repositories.py) → 封装 ORM / ES / Redis 操作
```

### 4.2 后端模块

| App | 关键模型 | 职责 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式、推理取消 |
| `common` | — | 中间件、异常体系、响应格式、SSE、Gateway、Rate Limiter、MinIO 封装 |
| `context` | — | PromptBuilder、上下文裁剪、Token 预算、监控 API、16 个 Jinja2 模板 |
| `graph` | — | LangGraph Agent 工厂、6 个 SubAgent、推理取消、GPU 锁 |
| `media` | MediaAttachment, DocumentChunkEmbedding | 媒体上传/下载、文档解析 + RAG 向量分块、过期清理 |
| `memory` | UserMemory, UserMemoryEmbedding | 记忆 CRUD、pgvector 混合搜索、Embedding、定时总结 |
| `models` | ModelConfig | LLM 模型配置（tool/multimodal/embedding）、SM4 加密 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权、SSO、成员管理 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | WebSocket 语音流、ASR/TTS 管道、声纹、设备、ambient |

### 4.3 Agent 数据流

```
chat/views.py → AgentService.execute()
  ├── build_prompt_preamble()  → 记忆召回 + 历史裁剪 + PromptBuilder
  ├── create_chat_agent()      → 主 Agent（含 SubAgent 工具）
  │     ├── search_subagent
  │     ├── memory_subagent
  │     ├── code_subagent
  │     ├── ha_subagent
  │     ├── multimodal_subagent
  │     └── document_subagent
  ├── SSE stream_events()      → 流式输出 + 工具调用追踪
  └── finalize_message()       → 消息持久化 + Langfuse 记录 + 监控推送
```

### 4.4 网络架构

```
公网请求 (HTTPS)
    ↓
frp 服务端 (infra.greydan.xin)
    ↓ wss://infra.greydan.xin:443
wstunnel client (127.0.0.1:7443)
    ↓ TCP
frpc (连接 127.0.0.1:7443)
    ↓
Nginx (8080)
    ├── /linchat/api/* → LinChat 后端 (8002)
    └── /linchat/*     → LinChat 前端 (3784)
```

关键特点：
- **抗 DPI**: frpc 流量经 wstunnel 封装为 WSS，规避深度包检测
- **systemd 管理**: frpc 和 wstunnel 均由 systemd 托管，自动重启
- **单入口**: Nginx 在 8080 端口统一分发，前后端共用域名

### 4.5 前端页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 聊天 | `/chat` | 主聊天 + 语音模式 + 监控面板 |
| 设置 | `/settings` | 模型配置 + 语音设置 + 声纹 + 设备 |
| 登录 | `/login` | 验证码登录 |
| 首页 | `/` | 重定向到 `/chat` |

---

## 5. 目标用户与使用场景

### 5.1 目标用户

| 用户类型 | 描述 | 权限 |
|----------|------|------|
| **家庭成员** | 固定家庭成员，永久有效 | 全部功能 + 用户管理 + 视角切换 |
| **访客** | 临时用户，可设置过期时间 | 仅基础聊天，无管理入口 |

### 5.2 典型使用场景

**日常对话**
- 与 AI 助手进行文本/语音对话
- AI 自动记忆用户偏好（居住地、职业、宠物名等），后续对话中主动召回

**知识检索**
- 上传 PDF 论文/文档 → AI 解析并建立向量索引 → 后续可语义检索文档内容
- 联网搜索获取实时信息

**智能家居**
- "把客厅灯调亮一些" → HA SubAgent 调用 Home Assistant API
- "空调温度调到 26 度" → 设备控制
- "卧室传感器什么状态？" → 设备诊断

**语音助手 (Jarvis 模式)**
- 环境持续监听 → 检测到有意义的话语 → 自动回应
- 支持声纹识别，区分不同家庭成员

**多模态理解**
- 上传图片询问内容
- 上传视频片段让 AI 描述
- 音频文件转写与分析

---

## 6. 项目里程碑

以下 15 个特性规范按开发顺序排列，全部已完成：

| # | 特性 | 规范路径 | 核心交付 |
|---|------|----------|----------|
| 001 | LLM 聊天页面 | `specs/001-llm-chat-page/` | 登录认证、消息收发、SSE 流式、LangGraph Agent 基础架构 |
| 002 | ASGI 异步视图 | — | Django 从 WSGI 切换到 ASGI（uvicorn），原生异步 SSE 视图 |
| 003 | 模型配置管理 | `specs/003-model-config/` | ModelConfig 模型 CRUD、SM4 加密 API Key、tool/multimodal/embedding 三类 |
| 004 | 上下文记忆 | `specs/004-context-memory/` | UserMemory + pgvector 向量搜索、混合检索（0.7 向量 + 0.3 关键词）、Embedding |
| 005 | 上下文监控 | `specs/005-context-monitoring/` | ContextMonitorPanel 前端面板、Token 消耗与工具调用实时展示 |
| 006 | SubAgent 工具 | `specs/006-subagent-tools/` | 主 Agent 拆分为 6 个 SubAgent（搜索/记忆/代码/HA/多模态/文档） |
| 007 | Home Assistant | `specs/007-home-assistant-tools/` | HA SubAgent 接入 Home Assistant API（设备查询/控制/诊断） |
| 008 | 多模态 MiniCPM | `specs/008-multimodal-minicpm/` | 图片/视频/音频/文档上传 + MiniCPM-o 模型推理 |
| 009 | 语音交互 | `specs/009-voice-interaction/` | 浏览器录音、WebSocket 双向通信、基础语音对话 |
| 010 | 语音 Agent 管道 | `specs/010-voice-agent-pipeline/` | Gateway ASR/TTS WebSocket 流式、VoiceConsumer 3 Mixin、声纹注册 |
| 011 | 文档 RAG | `specs/011-document-subagent-rag/` | Gateway 文档解析 + pgvector 向量分块 + 语义检索 |
| 012 | 解析进度展示 | `specs/012-doc-parse-progress/` | SSE 实时推送文档解析进度、前端进度条 |
| 013 | TTS 舒适队列 | `specs/013-tts-comfort-queue/` | TTS 播报排队机制，避免多段语音重叠 |
| 014 | Jarvis 环境语音 | `specs/014-jarvis-ambient-voice/` | Ambient 持续监听、话语聚合（3s 窗口）、响应决策引擎 |
| 015 | 家庭多用户 | `specs/015-family-multiuser/` | SysUser 扩展（成员/访客）、成员管理面板、视角切换、声纹注册 UI |

---

## 7. 关键设计决策

### 7.1 单用户单会话模型

**决策**: 一个用户永远对应一个会话。Message 模型中没有 `conversation_id`，只有 `user_id`。

**原因**:
- 家庭场景下每个用户只需要一个连续的对话流
- 简化数据模型和隔离逻辑
- 所有隔离操作（查询、并发锁、缓存键）统一按 `user_id` 粒度

**约束**:
- 禁止在任何模型或接口中引入 `conversation_id` / `session_id`
- 禁止使用"会话粒度"隔离

### 7.2 国密算法（SM3 / SM4）

**决策**: 密码使用 SM3 哈希，API Key 使用 SM4 对称加密存储。

**原因**: 符合国密标准要求。前后端共享 SM4 密钥，前端在传输前加密敏感字段。

**实现**: 后端 gmssl 库，前端 sm-crypto 库。

### 7.3 httpOnly Cookie 认证

**决策**: Token 存储在 httpOnly Cookie 中，禁止 localStorage。

**原因**: 防止 XSS 攻击窃取 Token。Axios 配置 `withCredentials` 自动携带 Cookie。

### 7.4 ASGI 强制（uvicorn）

**决策**: 禁止使用 `python manage.py runserver`，必须使用 `uvicorn core.asgi:application`。

**原因**: WSGI 模式不支持原生异步视图。SSE 流式响应和 WebSocket 语音通信均依赖 ASGI。Django Channels 也要求 ASGI 运行时。

### 7.5 LangGraph 多 Agent 架构

**决策**: 主 Agent 作为路由器，将任务分发到 6 个专业 SubAgent。

**原因**:
- 每个 SubAgent 有独立的工具集和超时配置
- 失败隔离：单个 SubAgent 超时不影响主 Agent
- 可独立测试和演进

**实现**: LangGraph `create_react_agent` 创建每个 SubAgent，主 Agent 通过 tool 调用触发。

### 7.6 Gateway 分离

**决策**: ASR、TTS、文档解析等计算密集型任务通过独立的 Gateway 服务处理，LinChat 后端仅作为 WebSocket/HTTP 客户端。

**原因**:
- GPU 资源与 Web 服务解耦
- Gateway 可独立扩缩容
- 通过 frpc STCP 安全连接远端 Gateway

### 7.7 Speckit 规范驱动开发

**决策**: 每个特性必须先编写 spec.md → plan.md → tasks.md，再进入开发。

**原因**:
- AI 代理（Claude）严格按规范实施，减少返工
- 宪法文件 (`constitution.md`) 定义不可违背的约束
- 可审计的开发过程

### 7.8 数据一致性策略

**决策**: PostgreSQL 为唯一可信数据来源，ES/Redis 通过 Celery 异步同步。

| 原则 | 说明 |
|------|------|
| 写操作原子性 | 事务保护，失败必须回滚 |
| 异步同步 | Celery 任务同步搜索索引和缓存 |
| 补偿机制 | 定时任务检查数据一致性 |

### 7.9 频率限制

| 场景 | 限制 |
|------|------|
| 匿名 API | 100 次/小时 |
| 认证 API | 1000 次/小时 |
| LLM 调用 | 60 次/分钟 |

### 7.10 LLM 异常统一处理

| 异常类型 | 策略 |
|----------|------|
| LLMConnectionError | 重试 3 次 |
| LLMTimeoutError | 重试 3 次 |
| LLMRateLimitError | 不重试，返回等待时间 |
| LLMContentFilterError | 不重试，允许用户修改 |

---

## 8. 快速上手

### 8.1 前置条件

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose
- 已配置的 `.env` 文件（`backend/.env`, `frontend/.env.local`）

### 8.2 启动步骤

```bash
# 1. 启动基础设施（PostgreSQL, Redis, Langfuse 等）
cd /home/dantsinghua/work/linchat
docker compose up -d

# 2. 启动 Nginx
sudo systemctl start nginx

# 3. 启动内网穿透（systemd 管理）
sudo systemctl start wstunnel
sudo systemctl start frpc

# 4. 构建前端（仅代码变更后需要）
cd /home/dantsinghua/work/linchat/frontend
npm run build

# 5. 启动应用服务（后端 + Celery + 前端）
cd /home/dantsinghua/work/linchat
./scripts/services.sh start

# 6. 验证
./scripts/services.sh status
```

### 8.3 开发命令

```bash
# 激活虚拟环境（后端开发必须）
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 后端测试
cd /home/dantsinghua/work/linchat/backend
pytest                                    # 全量（1278 用例）
pytest --cov=apps --cov-report=term-missing  # 带覆盖率

# 前端测试
cd /home/dantsinghua/work/linchat/frontend
npm test                                  # 单元测试
npm run test:e2e                          # E2E 测试
```

### 8.4 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端 | 3784 | Next.js 生产服务器 |
| 后端 | 8002 | uvicorn ASGI |
| Nginx | 8080 | 反向代理统一入口 |
| PostgreSQL | 5432 | 主数据库 |
| Redis | 6379 | 缓存 |

### 8.5 相关文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 开发指南 | `CLAUDE.md` | 开发规范、架构约束、禁止事项 |
| 项目宪法 | `.specify/memory/constitution.md` | 不可违背的原则 |
| 代码示例 | `docs/constitution-examples.md` | 编码时强制参考 |
| Gateway 集成 | `docs/linchat-integration-guide.md` | Gateway 接口对接 |
| 多模态 API | `docs/multimodal-api-guide.md` | 多模态接口文档 |
| TTS WebSocket | `docs/tts-websocket-api.md` | TTS 流式接口 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| **1 轮对话** | 1 条 user 消息 + 1 条 assistant 消息（1 对 user+assistant） |
| **保留最近 N 轮** | 保留最后 N x 2 条 user/assistant 消息 |
| **SubAgent** | 主 Agent 通过 tool 调用触发的专业子代理 |
| **Gateway** | 独立的 GPU 计算服务（ASR/TTS/文档解析/多模态推理） |
| **Ambient 模式** | 语音环境持续监听模式（Jarvis 风格） |
| **Speckit** | 规范驱动开发工具链（specify → plan → tasks → implement） |
| **成员** | 家庭固定用户，拥有完整权限 |
| **访客** | 临时用户，可设过期时间，权限受限 |
