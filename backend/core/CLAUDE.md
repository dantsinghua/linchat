# core 模块指南

> Django 项目核心配置模块。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `settings.py` | Django 全局配置（数据库、Redis、LLM Gateway、媒体、Celery、Langfuse） |
| `urls.py` | 顶层 URL 路由分发 |
| `asgi.py` | ASGI 应用入口（uvicorn 使用） |
| `wsgi.py` | WSGI 应用入口（已废弃，禁止使用） |
| `celery.py` | Celery 应用配置 + Beat 定时任务调度 |
| `redis.py` | 异步 Redis 客户端封装（连接池、频道名称工具） |

---

## 路由分发 (urls.py)

| 前缀 | 目标模块 | 说明 |
|------|---------|------|
| `api/v1/auth/` | `apps.common.urls` | 认证（验证码、登录/登出、用户信息） |
| `api/v1/chat/` | `apps.chat.urls` | 聊天（消息、流式、媒体、TTS、文档解析） |
| `api/v1/models/` | `apps.models.urls` | 模型配置管理 |
| `api/v1/memories/` | `apps.memory.urls` | 记忆 CRUD + 搜索 |
| `api/v1/events/` | `apps.common.views.sse_events` | SSE 事件流 |
| `api/v1/context/` | `apps.context.monitoring` | 上下文监控 API |

---

## 关键配置项 (settings.py)

### LLM Gateway

| 配置 | 说明 |
|------|------|
| `LLM_GATEWAY_URL` | Gateway 基础地址（如 `http://127.0.0.1:8100`） |
| `LLM_GATEWAY_API_KEY` | Gateway API 密钥 |
| `LLM_GATEWAY_INFERENCE_TIMEOUT` | 推理超时秒数（默认 180） |
| `LLM_MULTIMODAL_GATEWAY_URL` | 多模态 Gateway 地址 |
| `LLM_MULTIMODAL_MODEL` | 多模态模型名称（如 `minicpm-v`） |
| `LLM_MULTIMODAL_AUDIO_MODEL` | 音频推理模型名称（如 `minicpm-o`） |

### 媒体配置

| 配置 | 说明 |
|------|------|
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 连接凭据 |
| `MINIO_BUCKET_MEDIA` | 媒体文件桶名 |
| `MEDIA_MAX_FILE_SIZE` | 单文件最大大小 |
| `MEDIA_MAX_ATTACHMENTS` | 单次最多附件数 |
| `MEDIA_EXPIRATION_HOURS` | 媒体文件过期时间 |

### Celery Beat 定时任务

| 任务 | 调度 | 说明 |
|------|------|------|
| `chat.clean_expired_media` | 每小时 | 清理过期媒体文件 |
| `memory.retry_failed_embeddings` | 每 5 分钟 | 重试失败的 Embedding 生成 |
| `memory.generate_daily_summary` | 每天 00:00 | 每日记忆总结 |
| `memory.generate_monthly_summary` | 每月 1 日 00:00 | 每月记忆总结 |

---

## Redis 工具 (redis.py)

```python
from core.redis import get_redis, get_user_events_channel

client = await get_redis()  # 获取异步 Redis 客户端
channel = get_user_events_channel(user_id)  # → "events:user:{user_id}"
```
