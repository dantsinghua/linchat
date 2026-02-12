# chat/migrations 指南

## 迁移文件

| 迁移 | 内容 |
|------|------|
| `0001_initial.py` | 创建 `message` 表和 `langgraph_execution` 表 |
| `0002_alter_message_created_time.py` | 修改 `created_time` 去除 auto_now_add（改为服务层手动设置） |
| `0003_add_media_attachment.py` | 创建 `media_attachment` 表 |
| `0004_remove_thumbnail_add_document_type.py` | 移除缩略图字段，添加 `document` 媒体类型 |
