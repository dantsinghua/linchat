# chat/services/ 模块指南

## 模块结构

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `__init__.py` | 重新导出所有公共 API | `from .chat_service import ChatService, HistoryService` 等 |
| `types.py` | 数据类定义 | `StreamChunk`, `MessageVO`, `InferenceTask` |
| `chat_service.py` | 消息发送/历史查询 | `ChatService.send_message()`, `HistoryService` |
| `context_service.py` | 上下文压缩管理 | `ContextService` |
| `generation.py` | 活跃生成管理 + 异常映射 | `register_generation()`, `signal_stop()`, `map_llm_exception()` |
| `media_service.py` | 媒体文件上传/查询/校验 | `MediaService.upload()`, `MediaService.get_attachment()` |
| `minio_service.py` | MinIO 对象存储操作 | `MinioService.upload_file()`, `MinioService.get_presigned_url()` |
| `inference_service.py` | 推理任务管理/取消 | `InferenceService.submit()`, `InferenceService.cancel_task()` |
| `document_parse_service.py` | 文档解析服务（Gateway 三步流程） | `DocumentParseService.parse_document()`, `verify_task_ownership()`, `poll_task_status()`, `get_task_result()` |
| `tts_service.py` | TTS 语音合成（调用 Gateway `/v1/audio/speech`） | `TTSService.synthesize()`, `TTSError`, `tts_service`（单例） |

## 依赖关系

```
chat_service.py → generation.py, context_service.py, repositories.py, AgentService(graph)
media_service.py → minio_service.py, repositories.py (MediaAttachmentRepository)
inference_service.py → media_service.py, Redis (推理状态键)
document_parse_service.py → minio_service.py, repositories.py, EventService, httpx (Gateway), core.redis
tts_service.py → repositories.py (MessageRepository.get_by_uuid), httpx (Gateway)
```

## 多模态上传流程

1. 前端 `POST /api/v1/chat/media/upload/` 上传文件
2. `MediaService.upload()` 校验格式/大小 → `MinioService.upload_file()` 存入 MinIO
3. 创建 `MediaAttachment` 记录（含 UUID、MinIO 路径、过期时间）
4. 发消息时传 `attachments: [uuid1, uuid2, ...]`
5. `ChatService.send_message()` 将附件 UUID 传给 `AgentService.execute()`
6. Agent 判断 `is_multimodal=True` → `create_multimodal_direct()` 直连 Gateway

## 文档解析流程 (document_parse_service)

1. 前端 POST 传入 `attachment_uuid`
2. 校验附件所有权/类型/过期 → MinIO 下载 → Gateway `POST /v1/documents/parse`
3. Redis 所有权键 `doc_parse:{task_id}:owner` (TTL 7天)
4. 后台轮询推送 SSE `doc_parse_progress` 事件

## TTS 语音合成流程 (tts_service)

1. 查询消息（user_id 所有权校验）→ 校验 role=assistant、长度 ≤ 2000
2. 调用 Gateway `POST /v1/audio/speech` 返回音频字节流

## 推理任务管理 (inference_service)

- `submit()`: 注册推理任务到 Redis
- `cancel_task()`: 取消指定或最新的推理任务
- Redis 键: `inference:{user_id}:active`

## 测试 patch 路径

```python
@patch("apps.chat.services.media_service.minio_service")
@patch("apps.chat.services.media_service.media_repo")
@patch("apps.chat.services.minio_service.Minio")
@patch("apps.chat.services.inference_service.media_service")
@patch("apps.chat.services.tts_service.message_repo")
@patch("apps.chat.services.tts_service.httpx.AsyncClient")
@patch("core.redis.get_redis")  # document_parse_service
```
