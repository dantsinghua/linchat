# Backend 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

## 项目结构

```
backend/
├── core/                  # Django 项目配置（settings, urls, asgi, celery, redis）
├── apps/
│   ├── chat/              # 聊天核心（消息收发、SSE 流式、推理取消）
│   ├── common/            # 通用工具（中间件、异常、响应格式、SSE、Gateway、tokenizer、storage/）
│   ├── context/           # Prompt 构建与上下文裁剪、Token 预算管理、监控 API
│   ├── graph/             # LangGraph Agent（Agent 工厂、SubAgent、工具链、推理取消视图）
│   ├── media/             # 媒体附件（上传/下载、缩略图、文档解析、过期清理）
│   ├── memory/            # 用户记忆（CRUD、向量搜索、Embedding、定时总结）
│   ├── models/            # LLM 模型配置（文本/多模态 CRUD、SM4 加密密钥）
│   ├── users/             # 用户认证（验证码、登录/登出、Token、SSO）
│   └── voice/             # 语音交互（WebSocket 流、声纹、设备管理、响应决策）
├── tests/                 # 按模块组织: chat/ common/ context/ apps/graph/ memory/ models/ users/ voice/ integration/ performance/
├── scripts/               # 工具脚本（init_minio.py）
├── conftest.py            # pytest 全局配置（禁用限流）
├── pytest.ini             # pytest 配置（--reuse-db）
└── requirements.txt       # Python 依赖
```

## App 职责

| App | 关键模型 | 说明 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式响应、推理取消 |
| `common` | 无 | Token 中间件、异常体系、响应格式、SSE 事件、Gateway 调用、MinIO 存储封装 |
| `context` | 无 | Prompt 构建、上下文裁剪、Token 预算、监控 API |
| `graph` | 无 | LangGraph Agent 创建/执行、SubAgent（搜索/HA/多模态/文档解析）、推理取消 API |
| `media` | MediaAttachment | 媒体上传/下载、缩略图、文档解析路由、过期清理任务 |
| `memory` | Memory, MemorySummary | 用户记忆 CRUD、向量搜索、Embedding、每日/每月总结 |
| `models` | LLMModelConfig | LLM 模型配置 CRUD、SM4 加密密钥、活跃模型查询 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权、SSO、账户锁定 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹、设备、响应决策 |

## 关键依赖

| 类别 | 技术 |
|------|------|
| Web 框架 | Django 4.2+ / DRF 3.14+ / uvicorn 0.30+ |
| AI Agent | LangGraph + LangChain + langfuse |
| 任务队列 | Celery 5.3+ (Redis DB2) |
| 数据库 | PostgreSQL 15 + pgvector |
| 缓存 | Redis DB0 (django-redis) |
| 对象存储 | MinIO (minio SDK) |
| WebSocket | Django Channels 4.0+ (Redis DB3) |
| HTTP 客户端 | httpx (异步 Gateway 调用) |
| Gateway WS 客户端 | websockets 12.0+ (ASR 流式转录 + TTS 流式合成) |
| 国密算法 | gmssl (SM3 + SM4) |

## 常用命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 启动后端（必须 uvicorn）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002

# Celery
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# 测试
pytest                              # 全部
pytest tests/chat/ -v               # 单模块
pytest --cov=apps --cov-report=term-missing  # 覆盖率
```

## 架构约束

1. **三层架构**: views -> services -> repositories，禁止跨层
2. **用户隔离**: 所有操作按 `user_id` 粒度，不存在会话粒度
3. **ASGI 必须**: 禁止 `runserver`，必须 `uvicorn`
4. **统一响应**: `{"code": "...", "message": "...", "data": ...}`
