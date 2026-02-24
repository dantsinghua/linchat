# chat/migrations 指南

> `apps/chat` 模块的数据库迁移文件记录。

---

## 迁移文件

| 迁移 | 日期 | 内容 |
|------|------|------|
| `0001_initial.py` | 2026-01-25 | 创建 `message` 表和 `langgraph_execution` 表 |
| `0002_alter_message_created_time.py` | 2026-01-29 | 修改 `created_time` 去除 `auto_now_add`（改为服务层手动设置） |
| `0003_add_media_attachment.py` | 2026-02-07 | 创建 `media_attachment` 表（多模态附件） |
| `0004_remove_thumbnail_add_document_type.py` | 2026-02-12 | 移除 `thumbnail_path` 字段，`media_type` 增加 `document` 类型 |
| `0005_message_voice_fields.py` | 2026-02-24 | Message 表新增 `is_voice`（BooleanField, db_index）和 `speaker_id`（CharField 100, nullable）语音字段 |

---

## 表结构总览

### message 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | BigAutoField (PK) | 自增主键 |
| `message_uuid` | CharField(36, unique) | 消息 UUID（公开标识） |
| `user_id` | BigIntegerField (索引) | 用户 ID（数据隔离键） |
| `role` | CharField(20) | 角色: `user` / `assistant` / `system` |
| `content` | TextField | 消息内容 |
| `sequence` | IntegerField (索引) | 用户内递增序号（游标分页） |
| `status` | SmallIntegerField | 0=失败 / 1=正常 / 2=生成中 / 3=中断 |
| `request_id` | CharField(64, 索引, nullable) | 请求 ID（链路追踪） |
| `response_time_ms` | IntegerField (nullable) | 响应耗时（毫秒） |
| `prompt_tokens` | IntegerField | 提示 Token 数 |
| `completion_tokens` | IntegerField | 完成 Token 数 |
| `model_name` | CharField(100, nullable) | 模型名称 |
| `extra_data` | JSONField (nullable) | 扩展数据 |
| `is_voice` | BooleanField (索引) | 语音消息标记（default=False） |
| `speaker_id` | CharField(100, nullable) | 说话人ID（llmgateway声纹识别） |
| `created_time` | DateTimeField (索引) | 创建时间（服务层手动设置） |

**索引**: `idx_user_sequence` (user_id, sequence), `idx_user_created` (user_id, created_time), `idx_request_id` (request_id)

### langgraph_execution 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_id` | BigAutoField (PK) | 自增主键 |
| `execution_uuid` | CharField(36, unique) | 执行 UUID |
| `request_id` | CharField(64, 索引) | 关联消息的请求 ID |
| `user_id` | BigIntegerField (索引) | 用户 ID |
| `thread_id` | CharField(64, 索引) | 线程 ID（`user_{user_id}`） |
| `graph_name` | CharField(100) | 图名称 |
| `run_id` | CharField(64, nullable) | 运行 ID |
| `status` | CharField(20) | `pending` / `running` / `completed` / `failed` |
| `start_time` / `end_time` | DateTimeField | 执行时间 |
| `duration_ms` | IntegerField (nullable) | 执行耗时（毫秒） |
| `input_data` / `output_data` | JSONField (nullable) | 输入/输出数据 |
| `node_executions` | JSONField (nullable) | 节点执行详情 |
| `total_prompt_tokens` / `total_completion_tokens` | IntegerField | Token 统计 |
| `llm_call_count` | IntegerField | LLM 调用次数 |
| `error_type` / `error_message` | CharField/TextField (nullable) | 错误信息 |
| `langfuse_trace_id` / `langfuse_url` | CharField (nullable) | Langfuse 追踪 |

### media_attachment 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `attachment_id` | BigAutoField (PK) | 自增主键 |
| `attachment_uuid` | CharField(36, unique) | 公开标识 |
| `user_id` | BigIntegerField (索引) | 上传用户 ID |
| `message` | ForeignKey(Message, SET_NULL, nullable) | 关联消息 |
| `media_type` | CharField(20) | `image` / `video` / `audio` / `document` |
| `mime_type` | CharField(100) | MIME 类型 |
| `file_name` | CharField(255) | 原始文件名 |
| `file_size` | BigIntegerField | 文件大小（字节） |
| `storage_path` | CharField(500) | MinIO 存储路径 |
| `width` / `height` | IntegerField (nullable) | 尺寸（像素） |
| `duration_seconds` | FloatField (nullable) | 时长（秒） |
| `is_expired` | BooleanField | 是否已过期 |
| `created_at` | DateTimeField | 上传时间 |
| `expires_at` | DateTimeField | 过期时间 |

**索引**: `idx_attachment_user` (user_id), `idx_attachment_message` (message_id), `idx_attachment_expires` (expires_at, is_expired)

---

## 迁移变更说明

- **0002**: `created_time` 去除 `auto_now_add`，改为服务层在创建消息时手动设置时间，确保 user/assistant 消息对的时间戳一致性
- **0004**: 移除 `thumbnail_path` 字段（缩略图不再独立存储）；`media_type` 新增 `document` 选项以支持文档解析功能
