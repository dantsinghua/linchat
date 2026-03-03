# Media 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 独立媒体附件管理模块（010 从 `apps/chat` 分离），负责文件上传/下载、文档解析、过期清理。

---

## 文件清单

| 文件 | 职责 | 备注 |
|------|------|------|
| `models.py` | MediaAttachment 数据模型（表 `media_attachment`） | 从 chat 分离 |
| `repositories.py` | MediaAttachmentRepository（CRUD + 过期查询 + 消息关联） | 从 chat 分离 |
| `serializers.py` | MediaAttachmentSerializer + DocumentParseRequestSerializer | 从 chat 分离 |
| `views.py` | REST 视图：upload_media、get_media、parse_document、get_parse_task_status/result | 从 chat 分离 |
| `urls.py` | 媒体路由：`upload/`、`<uuid>/` | |
| `document_urls.py` | 文档解析路由：`parse/`、`tasks/<task_id>/`、`tasks/<task_id>/result/` | |
| `tasks.py` | Celery 定时任务：`clean_expired_media`（MinIO 文件清理，连续 10 次失败中止） | 从 chat 分离 |
| `services/__init__.py` | 导出：MediaService、MediaUploadError、DocumentParseService、DocumentParseError | |
| `services/upload.py` | 上传服务：文件校验 + MinIO 存储 + 元数据持久化（补偿删除机制） | 从 chat 分离 |
| `services/document.py` | 文档解析服务：Gateway 调用、轮询、SSE 进度通知、任务所有权验证 | 从 chat 分离 |
| `services/image.py` | 图片工具：Pillow 获取宽高 | 从 chat 分离 |
| `services/video.py` | 视频/音频工具：ffprobe 时长检测、ffmpeg 视频预处理 | 从 chat 分离 |
| `services/audio.py` | 音频工具：PCM 合并 WAV（merge_pcm_to_wav）、时长计算（calculate_duration）、重导出 get_audio_duration | 从 chat 分离 |
| `apps.py` | Django App 配置 | |
| `migrations/0001_initial.py` | 建表迁移（media_attachment） | |

---

## 核心模型 MediaAttachment（表 `media_attachment`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `attachment_id` | BigAutoField (PK) | 主键 |
| `attachment_uuid` | CharField(36, unique) | 公开标识 |
| `message` | FK -> chat.Message (SET_NULL) | 关联消息（可选） |
| `user_id` | BigIntegerField (db_index) | 上传用户 |
| `media_type` | CharField(20) | image/video/audio/document |
| `mime_type` | CharField(100) | MIME 类型 |
| `file_name` / `file_size` | 原始文件名 / 字节大小 | |
| `storage_path` | CharField(500) | MinIO 存储路径 |
| `width` / `height` | IntegerField (可选) | 像素尺寸（图片/视频） |
| `duration_seconds` | FloatField (可选) | 时长（音频/视频） |
| `is_expired` | BooleanField | 是否已过期 |
| `created_at` / `expires_at` | DateTimeField | 上传/过期时间 |

索引: idx_attachment_user (user_id), idx_attachment_message (message_id), idx_attachment_expires (expires_at, is_expired)

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/media/upload/` | 文件上传（MultiPart） |
| GET | `/api/v1/media/<uuid>/` | 文件下载（FileResponse） |
| POST | `/api/v1/documents/parse/` | 文档解析（返回 task_id，202） |
| GET | `/api/v1/documents/tasks/<task_id>/` | 解析任务状态 |
| GET | `/api/v1/documents/tasks/<task_id>/result/` | 解析结果（markdown/json） |

---

## 支持的文件类型与限制

| 类型 | MIME | 默认大小限制 | 额外限制 |
|------|------|-------------|---------|
| 图片 | jpeg/png/gif/webp | 10MB | 无 |
| 视频 | mp4/quicktime/webm | 50MB | 最长 60 秒 |
| 音频 | webm/wav/mpeg | 10MB | 1~60 秒 |
| 文档 | pdf/docx | 10MB | 无 |

---

## Repository 方法

| 方法 | 说明 |
|------|------|
| `create(attachment)` | 保存 MediaAttachment |
| `get_by_uuid(uuid, user_id)` | 按 UUID + user_id 查询 |
| `get_by_uuid_any_user(uuid)` | 按 UUID 查询（不限用户，内部用） |
| `get_by_uuids(uuids, user_id)` | 批量查询 |
| `update(attachment)` | 更新 |
| `associate_message(ids, message_id, user_id)` | 关联消息 |
| `find_expired(before_date, limit)` | 查找过期附件 |
| `mark_expired(ids)` | 批量标记过期 |

---

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.common.storage.minio_service` | MinIO 文件上传/下载/删除 |
| `apps.common.gateway_utils` | Gateway HTTP 请求 + Langfuse span 记录 |
| `apps.common.event_service` | 文档解析进度 SSE 推送（EventType.DOC_PARSE_PROGRESS） |
| `apps.common.sse` | first_validation_error 工具函数 |
| `core.redis` | 文档解析任务所有权键（doc_parse:{task_id}:owner，7天 TTL） |
| Pillow | 图片尺寸读取 |
| ffprobe/ffmpeg | 音视频时长检测和视频预处理 |

---

## 被依赖

| 模块 | 用途 |
|------|------|
| `apps.voice` | VoicePipeline 创建音频附件（MediaAttachment TYPE_AUDIO） |
| `apps.chat` | 消息关联附件（associate_attachments_to_message） |
| `apps.graph` | multimodal_subagent 文档解析工具 |

---

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/chat/test_media_service.py tests/chat/test_media_views.py tests/chat/test_document_parse_service.py -v
```
