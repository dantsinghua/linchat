# Media 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 独立媒体附件管理模块（从 `apps/chat` 分离），负责文件上传/下载、文档解析、过期清理。

---

## 文件清单

| 文件 | 职责 | 备注 |
|------|------|------|
| `models.py` | MediaAttachment 数据模型（表 `media_attachment`） | 从 chat 分离 |
| `repositories.py` | 数据访问层（CRUD + 过期查询 + 消息关联） | 从 chat 分离 |
| `serializers.py` | MediaAttachmentSerializer + DocumentParseRequestSerializer | 从 chat 分离 |
| `views.py` | REST 视图：上传、下载、文档解析、任务状态/结果查询 | 从 chat 分离 |
| `urls.py` | 媒体路由：`upload/`、`<uuid>/` | 新建 |
| `document_urls.py` | 文档解析路由：`parse/`、`tasks/<task_id>/`、`tasks/<task_id>/result/` | 新建 |
| `tasks.py` | Celery 定时任务：`clean_expired_media`（MinIO 文件清理） | 从 chat 分离 |
| `services/__init__.py` | 服务导出：MediaService、DocumentParseService | 新建 |
| `services/upload.py` | 上传服务：文件校验、MinIO 存储、元数据持久化 | 从 chat 分离 |
| `services/document.py` | 文档解析服务：Gateway 调用、轮询、SSE 通知 | 从 chat 分离 |
| `services/image.py` | 图片工具：Pillow 获取宽高 | 从 chat 分离 |
| `services/video.py` | 视频/音频工具：ffprobe 时长检测、ffmpeg 视频预处理 | 从 chat 分离 |
| `services/audio.py` | 音频工具：PCM 合并 WAV、时长计算（重导出 video.get_audio_duration） | 从 chat 分离 |
| `apps.py` | Django App 配置 | 新建 |
| `migrations/0001_initial.py` | 建表迁移 | 新建 |

---

## 核心模型 MediaAttachment

| 字段 | 类型 | 说明 |
|------|------|------|
| `attachment_id` | BigAutoField (PK) | 主键 |
| `attachment_uuid` | CharField(36, unique) | 公开标识 |
| `message` | FK -> chat.Message (SET_NULL) | 关联消息 |
| `user_id` | BigIntegerField (db_index) | 上传用户 |
| `media_type` | CharField(20) | image/video/audio/document |
| `mime_type` | CharField(100) | MIME 类型 |
| `file_name` / `file_size` | 原始文件名 / 字节大小 | |
| `storage_path` | CharField(500) | MinIO 存储路径 |
| `width` / `height` | 像素尺寸（图片/视频） | 可选 |
| `duration_seconds` | 时长（音频/视频） | 可选 |
| `is_expired` | BooleanField | 是否已过期 |
| `created_at` / `expires_at` | 上传/过期时间 | |

---

## 支持的文件类型与限制

| 类型 | MIME | 默认大小限制 | 额外限制 |
|------|------|-------------|---------|
| 图片 | jpeg/png/gif/webp | 10MB | 无 |
| 视频 | mp4/quicktime/webm | 50MB | 最长 60 秒 |
| 音频 | webm/wav/mpeg | 10MB | 1~60 秒 |
| 文档 | pdf/docx | 10MB | 无 |

---

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.common.storage.minio_service` | MinIO 文件操作 |
| `apps.common.gateway_utils` | Gateway HTTP 请求 + Langfuse span |
| `apps.common.event_service` | 文档解析进度 SSE 推送 |
| `core.redis` | 文档解析任务所有权键（doc_parse:{task_id}:owner） |
| Pillow | 图片尺寸读取 |
| ffprobe/ffmpeg | 音视频时长检测和预处理 |

---

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/chat/test_media_service.py tests/chat/test_media_views.py tests/chat/test_document_parse_service.py -v
```


<claude-mem-context>

</claude-mem-context>