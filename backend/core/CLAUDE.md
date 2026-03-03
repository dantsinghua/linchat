# core 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `settings.py` | Django 全局配置（数据库、Redis、LLM、媒体、Celery、Langfuse、安全、语音等） |
| `urls.py` | 顶层 URL 路由分发（`api/v1/` 前缀，含媒体和推理取消子路由） |
| `asgi.py` | ASGI 入口，ProtocolTypeRouter 分发 HTTP 和 WebSocket（语音）请求 |
| `wsgi.py` | WSGI 入口（已废弃，禁止使用） |
| `celery.py` | Celery 应用配置 + Beat 定时任务（5 个定时任务） |
| `redis.py` | 异步/同步 Redis 客户端封装 + 键名工具 + Pub/Sub 频道 |
| `__init__.py` | 导入 Celery app 确保启动时注册 |

## 路由分发 (urls.py)

| 路径前缀 | 目标模块 | 说明 |
|----------|---------|------|
| `admin/` | Django Admin | 后台管理 |
| `api/v1/auth/` | `apps.users.urls` | 认证（验证码、登录/登出） |
| `api/v1/chat/media/` | `apps.media.urls` | 媒体上传/下载/缩略图 |
| `api/v1/chat/documents/` | `apps.media.document_urls` | 文档解析 |
| `api/v1/chat/inference/` | `apps.graph.urls` | 推理取消 API |
| `api/v1/chat/` | `apps.chat.urls` | 消息收发、SSE 流式 |
| `api/v1/models/` | `apps.models.urls` | 模型配置管理 |
| `api/v1/memories/` | `apps.memory.urls` | 记忆 CRUD + 搜索 |
| `api/v1/voice/` | `apps.voice.urls` | 语音（声纹、设备、设置） |
| `api/v1/` (无前缀) | `apps.common.urls` | SSE 事件流 `/api/v1/events` |
| WebSocket `ws/voice/` | `apps.voice.routing` | 语音 WebSocket（通过 ASGI） |

## ASGI 配置 (asgi.py)

- `ProtocolTypeRouter` 区分 HTTP（Django ASGI）和 WebSocket
- WebSocket 经 `WebSocketTokenAuthMiddleware` 鉴权后路由到 voice consumer

## Redis 分配 (redis.py + settings.py)

| DB | 用途 |
|----|------|
| DB0 | django-redis 缓存 + 应用数据 |
| DB1 | Langfuse（外部） |
| DB2 | Celery Broker/Result |
| DB3 | Django Channels (WebSocket) |

## Celery 定时任务 (celery.py)

| 任务 | 调度 | 说明 |
|------|------|------|
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆总结 |
| `memory.generate_monthly_summary` | 每月 1 日 00:00 | 每月记忆总结 |
| `memory.embedding_health_check` | 每小时整点 | Embedding 健康检查 |
| `media.clean_expired_media` | 每日凌晨 3:00 | 清理过期媒体文件 |

## 关键配置分组 (settings.py)

| 分组 | 说明 |
|------|------|
| 数据库 | PostgreSQL，`DATABASE_URL` 解析，`CONN_MAX_AGE=60` |
| Redis | DB0 缓存，最大连接 50 |
| LLM 超时/重试 | 调用 60s、Agent 总 300s、SubAgent 60s、重试 3 次指数退避 |
| LLM Gateway | HTTP/WS 端点、推理/取消/轮询/文档解析超时 |
| MinIO | 端点、媒体/缩略图桶名 |
| 媒体限制 | 图片 10MB、视频 50MB、时长 60s、7 天过期 |
| 多模态 | max_tokens 1024、限流 60s、SubAgent 超时 20min |
| 语音 | 会话 120s、录音 30s、唤醒词"小鱼"、声纹阈值 0.5 |
| 认证 | Token 空闲 1h/绝对 24h、验证码 2min、锁定 15min |
| Memory | Embedding 1024 维、搜索 top5、向量权重 0.7 |
| 安全 | httpOnly Cookie、SM4 密钥、DRF 限流 100/h(匿名) 1000/h(认证) |


<claude-mem-context>
# Recent Activity

### Feb 12, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1003 | 4:26 PM | 🔵 | Complete LLM Gateway Configuration Settings | ~415 |
| #1002 | " | 🔵 | Django Settings LLM Gateway Configuration | ~271 |
</claude-mem-context>