# Backend 开发指南

> 本文件为 LinChat 后端的顶层开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 项目结构

```
backend/
├── core/                  # Django 项目配置（settings, urls, asgi, celery, redis）
├── apps/
│   ├── chat/              # 聊天核心模块（消息、流式响应、媒体上传、文档解析、推理取消）
│   ├── common/            # 通用工具（中间件、异常、响应格式、事件服务、Gateway 工具、tokenizer）
│   ├── context/           # Prompt 与上下文管理（构建器、裁剪器、监控、模板）
│   ├── graph/             # LangGraph Agent 模块（Agent 工厂、执行服务、SubAgent、工具链）
│   ├── memory/            # 用户记忆管理（CRUD、向量搜索、Embedding、定时总结）
│   ├── models/            # LLM 模型配置管理（文本/多模态模型 CRUD、密钥加密存储）
│   ├── users/             # 用户认证（验证码、登录/登出、Token、SSO）
│   ├── voice/             # 语音交互模块（WebSocket 语音流、声纹、设备管理、响应决策）
│   └── agent/             # （已废弃，Agent 逻辑已迁移至 graph 模块）
├── tests/                 # 测试目录（按模块组织：chat, users, common, models, memory, context, apps/graph）
├── scripts/               # 工具脚本（MinIO 初始化）
├── conftest.py            # pytest 全局配置（测试环境禁用限流）
├── manage.py              # Django 管理命令入口
├── pytest.ini             # pytest 配置（--reuse-db）
├── requirements.txt       # Python 依赖
├── langgraph.json         # LangGraph 图配置
└── .env                   # 环境变量（不入版本控制）
```

---

## App 列表与职责

| App | 路径 | 职责 | 关键模型 |
|-----|------|------|----------|
| `apps.chat` | `apps/chat/` | 消息收发、SSE 流式响应、媒体上传、文档解析、推理取消 | Message, MediaAttachment, LangGraphExecution |
| `apps.common` | `apps/common/` | Token 中间件、异常体系、响应格式、SSE 事件、Gateway 工具、tiktoken | 无（纯工具模块） |
| `apps.context` | `apps/context/` | Prompt 构建、上下文裁剪、Token 预算管理、上下文监控 API | 无 |
| `apps.graph` | `apps/graph/` | LangGraph Agent 创建/执行、SubAgent（网页搜索/HA/多模态/文档解析）、工具注册 | 无 |
| `apps.memory` | `apps/memory/` | 用户记忆 CRUD、向量搜索、Embedding 异步生成、每日/每月总结 | Memory, MemorySummary |
| `apps.models` | `apps/models/` | LLM 模型配置 CRUD、SM4 加密密钥存储、活跃模型查询 | LLMModelConfig |
| `apps.users` | `apps/users/` | 验证码、登录/登出、Token 鉴权、SSO 冲突、账户锁定 | SysUser |
| `apps.voice` | `apps/voice/` | WebSocket 语音流式交互、声纹注册与识别、设备管理、语音设置、响应决策 | SpeakerProfile, RegisteredDevice, VoiceSettings |

---

## 模块依赖关系

```
视图层 (views.py: chat / users / models / memory / common)
  └── 服务层 (services/)
        ├── chat/services/ ─────→ graph/agent (Agent 创建)
        │                  ─────→ context/ (Prompt 构建)
        │                  ─────→ common/gateway_utils (Gateway 调用)
        ├── memory/services/ ───→ common/tokenizer
        ├── models/services/ ───→ users/crypto (SM4 加密)
        ├── voice/services/ ───→ common/gateway_utils (Gateway WebSocket)
        │                  ───→ chat/repositories (消息持久化)
        │                  ───→ users/crypto (SM4 加密)
        └── users/services/ ────→ common/event_service (SSO 事件)
              └── 数据层 (repositories.py: ORM + @sync_to_async)
                    └── core/redis (异步/同步 Redis 客户端)
```

---

## 常用命令

```bash
# 激活虚拟环境（每次操作前必须执行）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 启动后端（必须用 uvicorn ASGI 模式）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002          # 生产
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload # 开发

# Celery Worker + Beat
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# 数据库迁移
python manage.py makemigrations
python manage.py migrate

# 初始化管理员账户
python manage.py init_admin

# MinIO Bucket 初始化
python scripts/init_minio.py

# 运行全部测试
pytest

# 运行指定模块测试
pytest tests/chat/ -v
pytest tests/users/ -v
pytest tests/models/ -v
pytest tests/memory/ -v
pytest tests/common/ -v
pytest tests/context/ -v
pytest tests/apps/graph/ -v
pytest tests/voice/ -v

# 带覆盖率
pytest --cov=apps --cov-report=term-missing

# 代码质量
black .
isort .
mypy .
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | Django 4.2+ / DRF 3.14+ |
| ASGI 服务器 | uvicorn 0.30+（必须，禁止 runserver） |
| AI Agent | LangGraph + LangChain (ChatOpenAI) |
| 任务队列 | Celery 5.3+ (Redis Broker DB2) |
| 数据库 | PostgreSQL 15 + pgvector |
| 缓存 | Redis DB0 (django-redis) |
| 对象存储 | MinIO (媒体文件 + 缩略图) |
| 监控 | Langfuse |
| 国密算法 | gmssl (SM3 哈希 + SM4 加密) |
| WebSocket | Django Channels 4.0+ (ASGI WebSocket) |
| HTTP 客户端 | httpx (异步 Gateway 调用) |
| Token 计数 | tiktoken (cl100k_base 编码) |
| 模板引擎 | Jinja2 (Prompt 模板) |
| 拼音匹配 | pypinyin (唤醒词模糊匹配) |

---

## 关键架构约束

1. **三层架构**: views -> services -> repositories，禁止跨层调用
2. **用户隔离**: 所有数据操作按 `user_id` 粒度隔离，不存在会话粒度
3. **异步优先**: SSE 视图使用 ASGI 原生异步，Repository 层 `@sync_to_async`
4. **ASGI 必须**: 禁止 `runserver`，必须使用 `uvicorn`
5. **国密加密**: SM3 密码哈希、SM4 传输/存储加密
6. **Token 安全**: httpOnly Cookie 存储，禁止 localStorage
7. **统一响应**: 所有 API 返回 `{"code": "...", "message": "...", "data": ...}` 格式

---

## Celery 定时任务

| 任务名 | 调度 | 说明 |
|--------|------|------|
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding 生成 |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆总结 |
| `memory.generate_monthly_summary` | 每月 1 日 00:00 | 每月记忆总结 |
| `memory.embedding_health_check` | 每小时整点 | Embedding 健康检查 |
| `chat.clean_expired_media` | 每日凌晨 3:00 | 清理过期媒体文件 |

---

## 测试配置

- **框架**: pytest + pytest-django + pytest-asyncio
- **配置文件**: `pytest.ini`（`--reuse-db` 复用测试数据库）
- **全局 conftest**: `conftest.py`（测试环境自动禁用 DRF 限流）
- **测试目录**: `tests/` 按模块组织子目录


<claude-mem-context>
# Recent Activity

### Feb 12, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #995 | 4:25 PM | 🔵 | Backend Environment Configuration Review | ~375 |
</claude-mem-context>