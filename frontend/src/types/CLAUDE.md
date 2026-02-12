# types 模块指南

## 类型文件

| 文件 | 内容 |
|------|------|
| `index.ts` | 核心类型（Message、ChatStreamEvent、HistoryResponse、ApiResponse） |
| `media.ts` | 媒体类型（MediaAttachment、UploadProgress、MediaType） |

## 关键类型

- `Message`: 聊天消息，status: 0=失败/1=正常/2=生成中/3=中断
- `ChatStreamEvent`: SSE 流事件，type: content/done/error/interrupted/context_compacting/context_compacted
  - `data` 字段携带 Gateway 错误信息（gateway_error、retry_after）
- `MediaAttachment`: 媒体附件元数据（uuid、media_type、file_name、预签名 URL）
