# chat/services/ 模块指南

## 模块结构

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `__init__.py` | 重新导出所有公共 API | `from .chat_service import ChatService, HistoryService` 等 |
| `types.py` | 数据类定义 | `StreamChunk`, `MessageVO` |
| `chat_service.py` | 消息发送/历史查询 | `ChatService.send_message()`, `HistoryService` |
| `context_service.py` | 上下文压缩管理 | `ContextService` |
| `generation.py` | 活跃生成管理 + 异常映射 | `register_generation()`, `signal_stop()`, `map_llm_exception()` |
| `media_service.py` | 媒体文件上传/查询 | `MediaService.upload()`, `MediaService.get_attachment()` |
| `minio_service.py` | MinIO 对象存储操作 | `MinioService.upload_file()`, `MinioService.get_presigned_url()` |
| `inference_service.py` | 多模态推理任务管理 | `InferenceService.submit()`, `InferenceService.cancel()` |
| `document_parse_service.py` | 文档解析服务（Gateway 三步流程） | `DocumentParseService.parse_document()`, `verify_task_ownership()`, `create_parse_task()`, `poll_task_status()`, `get_task_result()` |
| `tts_service.py` | TTS 语音合成服务（调用 Gateway `/v1/audio/speech`） | `TTSService.synthesize()`, `TTSError`, `tts_service`（单例） |

## 依赖关系

```
chat_service.py → generation.py, context_service.py, repositories.py, AgentService(graph)
media_service.py → minio_service.py, repositories.py (MediaAttachmentRepository)
inference_service.py → media_service.py
document_parse_service.py → minio_service.py, repositories.py, EventService, httpx (Gateway API), core.redis
tts_service.py → repositories.py (MessageRepository.get_by_uuid), httpx (Gateway API)
```

## 多模态上传流程

1. 前端通过 `POST /api/v1/chat/media/upload/` 上传文件
2. `MediaService.upload()` 校验格式/大小 → `MinioService.upload_file()` 存入 MinIO
3. 创建 `MediaAttachment` 记录（含 UUID、MinIO 路径、过期时间）
4. 发送消息时，前端传 `attachments: [uuid1, uuid2, ...]`
5. `ChatService.send_message()` 将附件 UUID 传给 `AgentService.execute()`
6. Agent 判断 `is_multimodal=True` → 使用 `create_multimodal_agent()` + 多模态网关

## MinIO 配置

- Bucket: `linchat-media`（媒体文件）、`linchat-thumbnails`（缩略图）
- 文件路径格式: `media/{user_id}/{date}/{uuid}.{ext}`
- 预签名 URL 有效期: 1 小时

## 文档解析流程 (document_parse_service)

1. 前端 POST `/api/v1/chat/documents/parse/` 传入 `attachment_uuid`
2. `DocumentParseService.parse_document()`:
   - 校验附件所有权、类型、过期状态
   - 从 MinIO 下载文件
   - 上传至 Gateway `POST /v1/documents/parse`
   - 写入 Redis 所有权键 `doc_parse:{task_id}:owner` (TTL 7天)
   - 启动后台轮询 `_poll_and_notify()` 推送 SSE 进度事件
3. `verify_task_ownership()`: status/result 视图调用，校验 Redis 所有权键
4. 前端通过 SSE `doc_parse_progress` 事件接收进度，完成后获取结果

## TTS 语音合成流程 (tts_service)

1. 前端 POST `/api/v1/chat/tts/` 传入 `message_uuid`
2. `TTSService.synthesize()`:
   - 通过 `MessageRepository.get_by_uuid()` 查询消息（含 user_id 所有权校验）
   - 校验 role=assistant、文本不为空、长度 ≤ 2000 字符
   - 调用 Gateway `POST /v1/audio/speech`（model=minicpm-o）返回音频字节流
3. Gateway 错误映射:
   - E3001 → TTS_MODEL_NOT_FOUND (404)
   - E3002 + retry_after → TTS_MODEL_SWITCHING (503)
   - E3002 无 retry_after → TTS_SERVICE_UNAVAILABLE (503)
   - E3003 → TTS_TIMEOUT (504)
   - httpx.TimeoutException → TTS_TIMEOUT (504)
   - httpx.ConnectError → TTS_SERVICE_UNAVAILABLE (503)

## 测试 patch 路径

```python
# media_service
@patch("apps.chat.services.media_service.minio_service")
@patch("apps.chat.services.media_service.media_repo")

# minio_service
@patch("apps.chat.services.minio_service.Minio")

# inference_service
@patch("apps.chat.services.inference_service.media_service")

# document_parse_service (verify_task_ownership 中 get_redis 是 lazy import)
@patch("core.redis.get_redis")

# inference_service 中 signal_stop 是 lazy import
@patch("apps.chat.services.generation.signal_stop")

# tts_service
@patch("apps.chat.services.tts_service.message_repo")
@patch("apps.chat.services.tts_service.httpx.AsyncClient")
```

<claude-mem-context>

</claude-mem-context>