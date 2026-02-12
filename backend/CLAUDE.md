# Backend 开发指南

> 本文件为 LinChat 后端的顶层开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 项目结构

```
backend/
├── core/                  # Django 项目配置（settings, urls, asgi, celery, redis）
├── apps/
│   ├── chat/              # 聊天核心模块（消息、流式响应、媒体、TTS、文档解析）
│   ├── common/            # 通用工具（中间件、异常、响应格式、事件服务、Gateway 工具）
│   ├── context/           # Prompt 与上下文管理（构建器、裁剪器、监控、模板）
│   ├── graph/             # LangGraph Agent 模块（Agent 工厂、执行服务、SubAgent、工具）
│   ├── memory/            # 用户记忆管理（CRUD、向量搜索、定时总结）
│   ├── users/             # 用户认证（验证码、登录/登出、Token、SSO）
│   └── models/            # LLM 模型配置管理
├── tests/                 # 测试目录（按模块组织）
├── scripts/               # 工具脚本（MinIO 初始化等）
├── conftest.py            # pytest 全局配置
├── manage.py              # Django 管理命令入口
├── pytest.ini             # pytest 配置
└── requirements.txt       # Python 依赖
```

---

## 模块依赖关系

```
views (chat/users/models/memory)
  └── services (chat/services/, memory/services, graph/services/)
        ├── repositories (chat/repositories, memory/repositories)
        ├── context (Prompt 构建 + 裁剪)
        ├── graph/agent (Agent 工厂 + 多模态直连)
        └── common (middleware, exceptions, event_service, gateway_utils)
```

---

## 常用命令

```bash
# 激活虚拟环境（每次操作前必须执行）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 启动后端（必须用 uvicorn ASGI 模式）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002

# Celery Worker + Beat
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# 数据库迁移
python manage.py makemigrations
python manage.py migrate

# 运行全部测试
pytest

# 运行指定模块测试
pytest tests/chat/ -v
pytest tests/memory/ -v

# 带覆盖率
pytest --cov=apps --cov-report=term-missing
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | Django 4.2+ / DRF 3.14+ |
| ASGI 服务器 | uvicorn 0.30+ |
| AI Agent | LangGraph + LangChain (ChatOpenAI) |
| 任务队列 | Celery 5.3+ (Redis Broker DB2) |
| 数据库 | PostgreSQL 15 + pgvector |
| 缓存 | Redis (DB0) |
| 对象存储 | MinIO (媒体文件) |
| 监控 | Langfuse |

---

## 关键架构约束

1. **三层架构**: views → services → repositories，禁止跨层调用
2. **用户隔离**: 所有数据操作按 `user_id` 粒度隔离，不存在会话粒度
3. **异步优先**: SSE 视图使用 ASGI 原生异步，Repository 层 `@sync_to_async`
4. **ASGI 必须**: 禁止 `runserver`，必须使用 `uvicorn`
5. **国密加密**: SM3 密码哈希、SM4 传输/存储加密
6. **Token 安全**: httpOnly Cookie 存储，禁止 localStorage
