# tests/chat 测试指南

> chat 模块测试集，覆盖服务层、视图层、数据层和多模态功能。

---

## 测试文件

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_services.py` | ChatService / HistoryService | `chat.services.chat_service` |
| `test_views.py` | HTTP 视图 + SSE 流 | `chat.views` |
| `test_concurrency.py` | 并发消息处理 | `chat.services.chat_service` |
| `test_media_service.py` | MediaService（上传/验证/过期） | `chat.services.media_service` |
| `test_media_views.py` | 媒体上传/获取视图 | `chat.views` |
| `test_media_attachment_repo.py` | MediaAttachmentRepository | `chat.repositories` |
| `test_media_cleanup_task.py` | Celery 媒体过期清理任务 | `chat.tasks` |
| `test_minio_service.py` | MinioService（对象存储操作） | `chat.services.minio_service` |
| `test_inference_service.py` | InferenceService（推理任务管理） | `chat.services.inference_service` |
| `test_inference_cancel.py` | 推理取消流程 | `chat.services.inference_service` |
| `test_model_routing.py` | 多模态模型路由逻辑 | `graph.agent` |
| `test_video_processing.py` | 视频预处理 + 多模态消息构建 | `graph.agent` |
| `test_audio_processing.py` | 音频多模态消息处理 | `graph.agent` |
| `test_document_parse_service.py` | DocumentParseService | `chat.services.document_parse_service` |
| `test_document_parse_views.py` | 文档解析视图 | `chat.views` |
| `test_tts_service.py` | TTSService | `chat.services.tts_service` |
| `test_tts_views.py` | TTS 视图 | `chat.views` |
| `test_context_service.py` | ContextService（上下文压缩） | `chat.services.context_service` |
| `test_prompts.py` | PromptBuilder | `graph.prompts` |
| `test_tools.py` | Agent 工具 | `graph.tools` |
| `test_agent.py` | Agent 创建/执行 | `graph.agent` |

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 chat 测试
pytest tests/chat/ -v

# 单个文件
pytest tests/chat/test_media_service.py -v

# 带覆盖率
pytest tests/chat/ --cov=apps/chat --cov-report=term-missing
```

---

## Mock 策略

所有外部依赖必须 mock:
- `message_repo` / `media_attachment_repo` / `execution_repo` — 数据库操作
- `minio_service` — MinIO 对象存储
- `AgentService.execute` — Agent 执行
- `httpx.AsyncClient` — 外部 HTTP 调用（TTS / DocumentParse / Gateway）
- `inference_service` — Redis 推理任务管理
- `EventService.publish_event` — SSE 事件推送
