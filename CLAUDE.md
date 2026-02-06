# Claude 开发指南

> 必须是用中文交流！！！每次回复我，都要叫我"安琳"，让我知道
> 本文件为 Claude AI 代理在本项目中的开发行为提供明确指导。
> 所有代码生成、修改和审查必须遵循本指南。

---

## 项目概述

**项目名称**: 大模型聊天平台 (LinChat)
**项目类型**: 企业级多租户 AI 聊天应用
**开发模式**: 规范驱动开发 (Speckit)

---

## ⚠️⚠️⚠️ 重要提醒 (必读！！！) ⚠️⚠️⚠️

> **🚨 警告 1：在执行任何 Python/Django 后端命令之前，必须先激活 linchat 虚拟环境！**
>
> **🚨 警告 2：本项目只有一套环境，不区分开发/生产环境，永远按生产环境对待！**
>
> **🚨 警告 3：前端必须先 `npm run build` 构建后再 `npm run start` 运行，不使用 `npm run dev`！**

### Python 虚拟环境

| 项目 | 虚拟环境路径 |
|------|-------------|
| **LinChat 后端** | `/home/dantsinghua/work/linchat/linchat/` |

### 激活命令

```bash
# ⚠️ 每次开发前必须执行！！！
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 验证激活成功 (应显示 linchat 虚拟环境)
which python
# 期望输出: /home/dantsinghua/work/linchat/linchat/bin/python
```

### 后端开发常用命令

```bash
# 激活虚拟环境后执行
cd /home/dantsinghua/work/linchat/backend

# ⚠️ ASGI 服务器启动（必须使用，支持原生异步 SSE 视图）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload  # 开发环境
uvicorn core.asgi:application --host 0.0.0.0 --port 8002           # 生产环境

# ❌ 禁止使用 runserver（WSGI 模式不支持原生异步 SSE 视图）
# python manage.py runserver 8002  # 已废弃，会导致 SSE 连接问题

# Django 管理命令
python manage.py migrate              # 数据库迁移
python manage.py makemigrations       # 生成迁移文件
python manage.py shell                # Django Shell

# 测试
pytest                                # 运行测试
pytest --cov=apps                     # 带覆盖率测试

# 代码质量
black .                               # 代码格式化
isort .                               # 导入排序
mypy .                                # 类型检查
```

### 前端命令

```bash
cd /home/dantsinghua/work/linchat/frontend

# ⚠️ 生产环境部署（必须使用）
npm run build                         # 构建生产版本
npm run start -- -p 3784              # 启动生产服务器

# 代码检查
npm run lint                          # 代码检查
npm test                              # 运行测试

# ❌ 不要使用 npm run dev，本项目只有生产环境！
```

---

## 网络架构配置

> **⚠️ 本节包含完整的网络配置信息，用于指导后续配置网络环境。**

### 服务端口总览

| 服务 | 端口 | 绑定地址 | 说明 |
|------|------|----------|------|
| **LinChat 前端** | 3784 | 0.0.0.0 | Next.js 生产服务器 |
| **LinChat 后端** | 8002 | 0.0.0.0 | Django 服务器 |
| **DeepTutor 前端** | 3783 | 0.0.0.0 | Next.js 服务器 |
| **DeepTutor 后端** | 8001 | 0.0.0.0 | Django 服务器 |
| **Nginx 主入口** | 3782, 8080 | 0.0.0.0 | 反向代理统一入口 |
| **Nginx Langfuse** | 8081 | 0.0.0.0 | Langfuse 独立端口 |
| **PostgreSQL** | 5432 | 0.0.0.0 | 主数据库 (LinChat + Langfuse) |
| **Redis** | 6379 | 0.0.0.0 | 缓存服务 (DB0: LinChat, DB1: Langfuse, DB2: Celery Broker) |
| **Langfuse Web** | 3100 | 127.0.0.1 | Langfuse Web 服务 (内部) |
| **ClickHouse HTTP** | 8123 | 127.0.0.1 | ClickHouse HTTP 接口 (仅本地) |
| **ClickHouse Native** | 9000 | 127.0.0.1 | ClickHouse Native 接口 (仅本地) |
| **MinIO S3 API** | 9010 | 127.0.0.1 | MinIO S3 接口 (仅本地) |
| **MinIO Console** | 9011 | 127.0.0.1 | MinIO 管理界面 (仅本地) |

### 公网访问地址

| 服务 | 公网地址 | 说明 |
|------|----------|------|
| **LinChat** | `http://www.greydan.xin/linchat` | 聊天应用主入口 |
| **LinChat API** | `http://www.greydan.xin/linchat/api/v1` | 后端 API |
| **DeepTutor** | `http://www.greydan.xin/` | DeepTutor 主入口 |
| **Langfuse** | `http://www.greydan.xin:8081` | LLM 监控平台 |
| **SSH** | `ssh -p 6022 www.greydan.xin` | SSH 远程连接 |

### 流量路径

```
公网请求
    ↓
frp 服务端 (120.25.192.185:7000)
    ↓
frp 客户端 (本地)
    ↓
Nginx (8080)
    ├── /linchat/api/* → LinChat 后端 (8002)
    ├── /linchat/*     → LinChat 前端 (3784)
    ├── /api/*         → DeepTutor 后端 (8001)
    └── /*             → DeepTutor 前端 (3783)

Nginx (8081)
    └── /*             → Langfuse Web (3100)
```

---

## frpc 内网穿透配置

**配置文件**: `/home/dantsinghua/frp/frpc.toml`

```toml
# frp 服务端连接
serverAddr = "120.25.192.185"
serverPort = 7000
auth.method = "token"
auth.token = "Dantsinghua!9871229"

# 日志配置
log.to = "./frpc.log"
log.level = "info"
log.maxDays = 7

# 穿透规则
[[proxies]]
name = "nas-vm-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 22
remotePort = 6022           # 公网 SSH 端口

[[proxies]]
name = "deeptutor-nginx"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8080            # Nginx 入口
remotePort = 6080           # 公网 HTTP 端口

[[proxies]]
name = "deeptutor-domain"
type = "http"
localIP = "127.0.0.1"
localPort = 8080
customDomains = ["www.greydan.xin", "greydan.xin"]
```

### frpc 管理命令

```bash
# 启动 frpc
cd /home/dantsinghua/frp
./frpc -c frpc.toml

# 后台运行
nohup ./frpc -c frpc.toml &

# 查看日志
tail -f /home/dantsinghua/frp/frpc.log
```

---

## Nginx 反向代理配置

**配置文件**: `/etc/nginx/sites-available/deeptutor`

### Upstream 定义

```nginx
# LinChat
upstream linchat_frontend { server 127.0.0.1:3784; keepalive 32; }
upstream linchat_backend  { server 127.0.0.1:8002; keepalive 32; }

# DeepTutor
upstream frontend { server 127.0.0.1:3783; keepalive 32; }
upstream backend  { server 127.0.0.1:8001; keepalive 32; }

# Langfuse
upstream langfuse_web { server 127.0.0.1:3100; keepalive 32; }
```

### 路由规则

| 监听端口 | 路径 | 目标服务 |
|----------|------|----------|
| 3782/8080 | `/linchat/api/*` | linchat_backend (8002) |
| 3782/8080 | `/linchat/*` | linchat_frontend (3784) |
| 3782/8080 | `/api/*` | deeptutor_backend (8001) |
| 3782/8080 | `/*` | deeptutor_frontend (3783) |
| 8081 | `/*` | langfuse_web (3100) |

### Nginx 管理命令

```bash
# 测试配置
sudo nginx -t

# 重载配置
sudo nginx -s reload

# 查看状态
sudo systemctl status nginx
```

---

## Docker 服务配置

**配置文件**: `/home/dantsinghua/work/linchat/docker-compose.yml`

### 服务列表

| 容器名称 | 镜像 | 说明 |
|----------|------|------|
| linchat-postgres | postgres:15-alpine | PostgreSQL 主数据库 |
| linchat-redis | redis/redis-stack-server:latest | Redis 缓存 |
| linchat-clickhouse | clickhouse/clickhouse-server:24.3 | ClickHouse 分析库 |
| linchat-minio | minio/minio:latest | MinIO 对象存储 |
| linchat-langfuse-web | langfuse/langfuse:3 | Langfuse Web |
| linchat-langfuse-worker | langfuse/langfuse-worker:3 | Langfuse Worker |

### Docker 管理命令

```bash
cd /home/dantsinghua/work/linchat

# 启动所有服务
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f [服务名]

# 重启单个服务
docker compose restart [服务名]

# 停止所有服务
docker compose down
```

---

## 环境变量配置

### 后端环境变量 (`backend/.env`)

```bash
DATABASE_URL=postgresql://postgres:linchat_123@localhost:5432/linchat
REDIS_URL=redis://localhost:6379/0
DJANGO_SECRET_KEY=linchat-dev-secret-key-change-in-production
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,www.greydan.xin
CORS_ALLOWED_ORIGINS=http://localhost:3784,http://127.0.0.1:3784,http://www.greydan.xin
SM4_SECRET_KEY=linchat-sm4-key!

# LLM 配置 (火山引擎 DeepSeek)
LLM_API_BASE=https://ark.cn-beijing.volces.com/api/v3
LLM_API_KEY=<your-api-key>
LLM_MODEL_NAME=deepseek-v3-1-terminus
```

### 前端环境变量 (`frontend/.env.local`)

```bash
NEXT_PUBLIC_API_BASE_URL=/linchat/api/v1
NEXT_PUBLIC_SM4_KEY=linchat-sm4-key!
```

### Langfuse 访问凭据

```
URL: http://www.greydan.xin:8081
Email: admin@linchat.local
Password: Admin@123456
```

---

## 服务启动顺序

```bash
# 1. 启动 Docker 服务 (PostgreSQL, Redis, Langfuse 等)
cd /home/dantsinghua/work/linchat
docker compose up -d

# 2. 启动 Nginx
sudo systemctl start nginx

# 3. 启动 frpc
cd /home/dantsinghua/frp
nohup ./frpc -c frpc.toml &

# 4. 启动 LinChat 后端 (⚠️ 必须使用 uvicorn ASGI 模式)
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
nohup uvicorn core.asgi:application --host 0.0.0.0 --port 8002 > /tmp/linchat-backend.log 2>&1 &

# 5. 启动 LinChat 前端 (必须先 build)
cd /home/dantsinghua/work/linchat/frontend
npm run build
nohup npm run start -- -p 3784 &
```

---

## 故障排查

### 检查服务状态

```bash
# 检查 Docker 服务
docker compose ps

# 检查 Nginx
sudo systemctl status nginx
curl http://localhost:8080/nginx-health

# 检查 frpc
ps aux | grep frpc
tail -f /home/dantsinghua/frp/frpc.log

# 检查端口占用
ss -tlnp | grep -E '(3784|8002|8080|5432|6379)'
```

### 常见问题

| 问题 | 解决方案 |
|------|----------|
| 前端 404 | 检查是否执行了 `npm run build`，检查 Nginx 配置 |
| API 502 | 检查后端是否启动，检查虚拟环境是否激活 |
| 数据库连接失败 | 检查 Docker PostgreSQL 是否运行 |
| 公网无法访问 | 检查 frpc 是否运行，检查防火墙规则 |

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | Django REST Framework 4.2+ |
| ASGI 服务器 | uvicorn 0.30+ (必须，支持原生异步视图) |
| 前端框架 | Next.js 14+ / React 18+ / TypeScript 5.0+ |
| 主数据库 | PostgreSQL (唯一可信来源) |
| 搜索引擎 | Elasticsearch (只读副本) |
| 缓存层 | Redis (会话/缓存/实时通信) |
| 任务队列 | Celery 5.3+ |
| AI Agent | LangGraph + Langfuse |
| 状态管理 | Zustand (前端) |

---

## 强制参考文档

在进行任何开发工作前，**必须**阅读以下文档：

### 核心治理文档

| 文档 | 路径 | 用途 |
|------|------|------|
| 项目宪法 | [.specify/memory/constitution.md](.specify/memory/constitution.md) | 不可违背的原则和约束 |
| 代码示例 | [docs/constitution-examples.md](docs/constitution-examples.md) | 编码时强制参考的示例代码 |

### 特性规范文档

| 文档类型 | 路径模式 | 用途 |
|----------|----------|------|
| 特性规范 | `specs/<feature>/spec.md` | 功能需求和验收标准 |
| 实施计划 | `specs/<feature>/plan.md` | 技术方案和实施步骤 |
| 任务清单 | `specs/<feature>/tasks.md` | 具体开发任务 |
| 质量检查 | `specs/<feature>/checklists/*.md` | 各阶段质量检查清单 |

---

## 开发工作流

### Speckit 命令参考

```bash
# 特性规范阶段
/speckit.specify    # 创建/更新特性规范
/speckit.clarify    # 澄清规范中的歧义

# 规划阶段
/speckit.plan       # 生成实施计划
/speckit.tasks      # 生成任务清单

# 验证阶段
/speckit.analyze    # 跨文档一致性分析
/speckit.checklist  # 生成质量检查清单

# 实施阶段
/speckit.implement  # 按任务清单实施

# 治理阶段
/speckit.constitution  # 更新项目宪法
```

### 开发流程

```
1. 阅读宪法 → 2. 阅读规范 → 3. 阅读计划 → 4. 执行任务 → 5. 验证合规
```

---

## 编码规范速查

### Python/Django 后端

| 规范项 | 要求 | 参考 |
|--------|------|------|
| 代码风格 | PEP 8 + Black (88字符) | 宪法 2.1 |
| 导入排序 | isort | 宪法 2.1 |
| 类型注解 | 所有公共函数必须添加 | 宪法 2.1 |
| 文档字符串 | Google 风格 | 宪法 2.1 |
| 数据一致性 | 事务保护，失败回滚 | 代码示例 1-2节 |
| 异常处理 | 自定义异常类层级 | 代码示例 3节 |
| 测试覆盖 | 服务层 95%，总体 80%+ | 代码示例 4-6节 |

### TypeScript/Next.js 前端

| 规范项 | 要求 | 参考 |
|--------|------|------|
| 代码风格 | ESLint + Prettier | 宪法 2.2 |
| 类型模式 | 严格模式 | 宪法 2.2 |
| 组件规范 | 函数式组件 + Hooks | 宪法 2.2 |
| Props 定义 | 必须使用 interface | 宪法 2.2 |
| 状态管理 | Zustand + React Query | 宪法 2.2 |

---

## 强制术语定义 (不可违背)

| 术语 | 定义 |
|------|------|
| **1 轮对话** | 1 条 role=user 消息 + 1 条 role=assistant 消息（1 对 user+assistant 消息） |
| **保留最近 N 轮** | 保留最后 N×2 条 user/assistant 消息（例：2 轮 = 4 条消息） |
| **隔离粒度** | 永远按 `user_id` 粒度，不存在"会话粒度"或"session 粒度" |

---

## 架构约束 (不可违背)

### 分层架构

```
视图层 (views.py)      → 仅处理 HTTP 请求响应，禁止业务逻辑
服务层 (services.py)   → 封装所有业务逻辑 ★核心
数据层 (repositories.py) → 封装 ORM/ES/Redis 操作
```

### 数据一致性

| 原则 | 说明 |
|------|------|
| PostgreSQL 为主 | 唯一可信数据来源 |
| 写操作原子性 | 失败必须回滚 |
| 同步机制 | ES/Redis 通过 Celery 异步同步 |
| 补偿机制 | 必须实现数据一致性检查 |

> **参考**: 代码示例文档 1-2 节

### 大模型异常处理

必须统一处理以下异常类型：

| 异常 | 策略 |
|------|------|
| LLMConnectionError | 重试3次 |
| LLMTimeoutError | 重试3次 |
| LLMRateLimitError | 不重试，返回等待时间 |
| LLMContentFilterError | 不重试，允许用户修改 |

> **参考**: 代码示例文档 3 节

---

## 安全要求 (不可违背)

| 类别 | 要求 |
|------|------|
| 令牌存储 | httpOnly Cookie (禁止 localStorage) |
| 密码哈希 | 国密SM3算法 |
| API 密钥 | 国密SM4加密存储 |
| 频率限制 | 匿名100次/时，认证1000次/时，LLM 60次/分 |

---

## 测试要求

| 测试类型 | 说明 | 工具 |
|----------|------|------|
| 单元测试 | 隔离执行，mock 外部依赖 | pytest / Jest |
| 集成测试 | 真实数据库，mock 外部服务 | pytest-django / MSW |
| 端到端 | 完整用户流程 | Playwright |

**覆盖率要求**:
- 总体 ≥ 80%
- 关键路径 ≥ 95%
- 服务层 ≥ 95%

> **参考**: 代码示例文档 4-6 节

---

## 性能指标

| 场景 | 指标 |
|------|------|
| API GET 请求 | p95 < 200ms |
| API POST 请求 | p95 < 300ms |
| 大模型首令牌 | < 2秒 |
| 前端 FCP | < 1.5秒 |
| 前端打包 | < 200KB (gzip) |

---

## 提交规范

```
<类型>(<范围>): <描述>

类型: feat / fix / docs / style / refactor / perf / test / chore
示例: feat(chat): 添加流式响应支持
```

---

## 禁止事项

1. **禁止**在视图层编写业务逻辑
2. **禁止**直接写原生 SQL (必须使用 ORM)
3. **禁止**将 Token 存储在 localStorage（必须使用 httpOnly Cookie）
4. **禁止**提交敏感信息到版本控制
5. **禁止**合并违反"不可违背"条款的代码
6. **禁止**跳过测试直接部署
7. **禁止**忽略数据一致性检查
8. **禁止**在 SSE 视图中手动创建临时事件循环（必须使用 ASGI 原生异步视图）
9. **禁止**使用 `python manage.py runserver` 启动后端（必须使用 uvicorn ASGI 模式）
10. **禁止**使用"会话粒度"隔离 — 本项目所有隔离操作（数据查询、并发锁、缓存键）永远按 `user_id` 粒度，不存在"会话粒度"或"session 粒度"概念

---

## 当前特性

| 特性分支 | 规范路径 | 状态 |
|----------|----------|------|
| 001-llm-chat-page | [specs/001-llm-chat-page/spec.md](specs/001-llm-chat-page/spec.md) | 规范已完成 |

---

## 快速参考链接

- 宪法文件: [.specify/memory/constitution.md](.specify/memory/constitution.md)
- 代码示例: [docs/constitution-examples.md](docs/constitution-examples.md)
- 当前特性规范: [specs/001-llm-chat-page/spec.md](specs/001-llm-chat-page/spec.md)
- 规范质量检查: [specs/001-llm-chat-page/checklists/requirements.md](specs/001-llm-chat-page/checklists/requirements.md)

---

*本文件随项目演进持续更新，版本与宪法文件同步。*

## Active Technologies
- Python 3.11+ (后端) / TypeScript 5.0+ (前端) (001-llm-chat-page)
- Python 3.11+ + Django 4.2+, DRF 3.14+, uvicorn 0.30+, redis-py (async) (002-asgi-async-views)
- PostgreSQL (主存储), Redis (缓存/Pubsub) (002-asgi-async-views)
- Python 3.11+ (后端) + Django 4.2+, DRF 3.14+, uvicorn 0.30+, Celery 5.3+, tiktoken, pgvector, openai SDK, Langfuse (004-context-memory)
- PostgreSQL 15 + pgvector 扩展（主存储）, Redis（缓存/分布式锁/Celery Broker） (004-context-memory)
- Python 3.11+ (后端) / TypeScript 5.0+ (前端) + Django 4.2+, DRF 3.14+, uvicorn 0.30+, LangGraph, LangChain, tiktoken, pgvector, openai SDK, Celery 5.3+, Langfuse (004-context-memory)
- PostgreSQL 15 + pgvector + pg_jieba (主存储), Redis (缓存/分布式锁/Celery Broker DB2) (004-context-memory)
- Python 3.11+ (后端) / TypeScript 5.0+ (前端) + Django 4.2+, DRF 3.14+, uvicorn 0.30+, LangGraph, tiktoken, redis-py (async), Next.js 14+, React 18+, Zustand (005-context-monitoring)
- PostgreSQL 15 (主存储), Redis (缓存/PubSub/Celery Broker) (005-context-monitoring)
- PostgreSQL 15 (主存储), Redis (缓存/PubSub/Celery Broker DB2) (005-context-monitoring)
- Python 3.11+ (后端) + Django 4.2+, DRF 3.14+, LangGraph, LangChain, httpx, redis-py (async) (006-home-assistant-tools)
- Redis（限流/缓存键） (006-home-assistant-tools)
- Python 3.11+ (后端) + Django 4.2+, DRF 3.14+, LangGraph (create_react_agent), LangChain (ChatOpenAI, tool decorator), uvicorn 0.30+, redis-py (async), httpx (006-subagent-tools)
- PostgreSQL 15 (主存储), Redis (缓存/PubSub) (006-subagent-tools)
- Python 3.11+ + Django 4.2+, LangGraph, LangChain, httpx, redis-py (async) (007-home-assistant-tools)
- Redis（速率限制键） (007-home-assistant-tools)

## Recent Changes
- 001-llm-chat-page: Added Python 3.11+ (后端) / TypeScript 5.0+ (前端)
