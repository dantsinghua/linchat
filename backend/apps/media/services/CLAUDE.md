# Media Services 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> `apps/media/services/` 媒体处理业务逻辑层（010 从 apps/chat 分离）。

---

## 文件清单

| 文件 | 职责 | 全局实例 |
|------|------|---------|
| `__init__.py` | 导出 MediaService、MediaUploadError、DocumentParseService、DocumentParseError | — |
| `upload.py` | 文件校验（类型/大小/时长）、MinIO 存储、元数据持久化、补偿删除 | `media_service` |
| `document.py` | 文档解析：Gateway 调用、轮询、SSE 进度通知、任务所有权验证 | `document_parse_service` |
| `image.py` | 图片尺寸读取（Pillow） | 无（纯函数） |
| `video.py` | 视频/音频时长检测（ffprobe）、视频预处理（ffmpeg 降分辨率+帧率） | 无（纯函数） |
| `audio.py` | PCM 合并 WAV（merge_pcm_to_wav）、时长计算（calculate_duration）、重导出 get_audio_duration | 无（纯函数） |

---

## MediaService（upload.py）

| 方法 | 说明 |
|------|------|
| `validate_file(file_name, mime_type, file_size)` | 校验文件类型和大小，返回 media_type |
| `upload(user_id, file_data, file_name, mime_type, file_size)` | 完整上传流程（校验 → 元数据提取 → MinIO → DB），返回 MediaAttachment |
| `get_attachment(uuid, user_id)` | 按 UUID + user_id 查询 |
| `get_attachment_any_user(uuid)` | 按 UUID 查询（内部用，如文档解析所有权验证前） |
| `get_attachments_by_uuids(uuids, user_id)` | 批量查询 |
| `get_media_file(attachment)` | 从 MinIO 下载文件（过期检查） |
| `associate_attachments_to_message(uuids, message_id, user_id)` | 关联附件到消息 |

补偿机制: DB 写入失败时自动删除已上传的 MinIO 文件。

---

## DocumentParseService（document.py）

| 方法 | 说明 |
|------|------|
| `parse_document(user_id, attachment_uuid, pages)` | 完整解析流程：附件校验 → MinIO 下载 → Gateway 创建任务 → Redis 存所有权 → 后台轮询+通知 |
| `create_parse_task(file_data, file_name, model, pages)` | Gateway POST /v1/documents/parse |
| `poll_task_status(task_id)` | Gateway GET /v1/documents/tasks/{task_id} |
| `get_task_result(task_id, format)` | Gateway GET /v1/documents/tasks/{task_id}/result |
| `verify_task_ownership(task_id, user_id)` | Redis 检查任务归属（doc_parse:{task_id}:owner） |

后台轮询: `_poll_and_notify()` — asyncio.create_task，每 DOC_PARSE_POLL_INTERVAL 秒检查一次，最多 DOC_PARSE_POLL_MAX_WAIT 秒，通过 EventService.publish_event 推送 SSE 进度。

---

## 音频工具（audio.py）

| 函数 | 说明 |
|------|------|
| `merge_pcm_to_wav(pcm_chunks, sample_rate, channels, sample_width)` | PCM 帧列表合并为 WAV 字节 |
| `calculate_duration(pcm_data, sample_rate, channels, sample_width)` | 计算 PCM 数据时长（秒） |
| `get_audio_duration(file_bytes)` | 重导出自 video.py，ffprobe 检测音频时长 |

---

## 视频工具（video.py）

| 函数 | 说明 |
|------|------|
| `get_video_duration(file_bytes)` | ffprobe 检测视频时长 |
| `get_audio_duration(file_bytes)` | ffprobe 检测音频时长 |
| `preprocess_video(file_bytes, max_width, fps)` | ffmpeg 视频预处理（降分辨率/帧率，去音轨） |
