# Data Model: 012-doc-parse-progress

**Date**: 2026-03-06

> 本特性不引入新数据库模型。核心数据结构为 SSE 事件载荷和前端状态对象。

## SSE 事件载荷（后端 → 前端）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"doc_parse_progress"` |
| `task_id` | string | 是 | Gateway 解析任务 ID |
| `status` | enum | 是 | `pending` / `processing` / `completed` / `incomplete` / `failed` |
| `progress` | object | 是 | `{ "current": number, "total": number }` |
| `file_name` | string | 是 | 原始文件名（如 `2509.04664v1.pdf`） |
| `suggestion` | string? | 否 | Gateway 建议信息（`incomplete`/`failed` 时有值） |
| `error_message` | string? | 否 | 错误描述（`failed` 时有值） |

## 前端全局状态（chatStore）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `docParseProgress` | `DocParseProgress \| null` | `null` | 当前文档解析进度 |

### DocParseProgress 接口

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务标识 |
| `status` | enum | 5 种状态 |
| `current` | number | 当前已解析页数 |
| `total` | number | 总页数 |
| `fileName` | string | 文件名 |
| `suggestion` | string? | Gateway 建议 |
| `errorMessage` | string? | 错误信息 |

## 状态转换

```
null → pending → processing → completed → null (1.5s 延迟)
                            → incomplete → null (3s 延迟)
                            → failed → null (3s 延迟)
       pending → failed (超时) → null (3s 延迟)
```

## 复用的已有实体（不修改）

- `EventType.DOC_PARSE_PROGRESS`：已在 `apps/common/event_service.py` 中定义
- `EventService.publish_event()`：已有的 Redis Pub/Sub 推送方法
- `DocumentParseService.poll_task_status()`：已有的 Gateway 轮询方法（仅添加重试逻辑）
