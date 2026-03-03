# chat/migrations 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 迁移文件

| 迁移 | 内容 |
|------|------|
| `0001_initial.py` | 创建 `message` 表和 `langgraph_execution` 表 |
| `0002_alter_message_created_time.py` | `created_time` 去除 `auto_now_add`，改为服务层手动设置 |
| `0003_add_media_attachment.py` | 创建 `media_attachment` 表（多模态附件） |
| `0004_remove_thumbnail_add_document_type.py` | 移除 `thumbnail_path`，`media_type` 增加 `document` |
| `0005_message_voice_fields.py` | Message 新增 `is_voice`(BooleanField, db_index) 和 `speaker_id`(CharField 100, nullable) |
| `0006_remove_mediaattachment.py` | **状态迁移**：将 MediaAttachment 从 chat app 状态移除（不执行 SQL），表由 `media` app 管理 |
| `0007_alter_langgraphexecution_duration_ms_and_more.py` | LangGraphExecution 和 Message 所有字段 AlterField（规范化 blank/null 属性） |

---

## 当前表结构

### message 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | BigAutoField (PK) | 自增主键 |
| `message_uuid` | CharField(36, unique) | 消息 UUID |
| `user_id` | BigIntegerField (索引) | 用户 ID（数据隔离键） |
| `role` | CharField(20) | `user` / `assistant` / `system` |
| `content` | TextField | 消息内容 |
| `sequence` | IntegerField (索引) | 用户内递增序号 |
| `status` | SmallIntegerField | 0=失败 / 1=正常 / 2=生成中 / 3=中断 |
| `request_id` | CharField(64, nullable) | 请求 ID |
| `prompt_tokens` / `completion_tokens` | IntegerField | Token 统计 |
| `model_name` | CharField(100, nullable) | 模型名称 |
| `is_voice` | BooleanField (索引) | 语音消息标记 |
| `speaker_id` | CharField(100, nullable) | 说话人 ID |
| `created_time` | DateTimeField (索引) | 创建时间（服务层手动设置） |

索引: `idx_user_sequence`, `idx_user_created`, `idx_request_id`

### langgraph_execution 表

Agent 执行监控记录。含 `execution_uuid`, `request_id`, `user_id`, `thread_id`, `graph_name`, `status`, `duration_ms`, `input_data/output_data/node_executions`(JSON), Token 统计, Langfuse 追踪。

### media_attachment 表

**注意**: 该表现由 `apps/media/` 模块管理（0006 迁移将模型从 chat 状态中移除）。表结构详见 `apps/media/` 文档。
