# SSE Event Contract: doc_parse_progress

**Date**: 2026-03-06

> 本特性不新增 REST API 端点。唯一的契约是 SSE 事件格式。

## 事件通道

- **SSE 端点**: `GET /api/v1/events`（已有，不修改）
- **Redis 频道**: `user:{user_id}:events`（已有，不修改）
- **事件名称**: `doc_parse_progress`（已在 EventType 枚举中定义）

## 事件格式

```
event: doc_parse_progress
data: {"type":"doc_parse_progress","task_id":"abc123","status":"processing","progress":{"current":5,"total":36},"file_name":"2509.04664v1.pdf","suggestion":null,"error_message":null}
```

## 状态定义

| status | 含义 | progress | suggestion | error_message |
|--------|------|----------|------------|---------------|
| `pending` | 排队等待 | `{current: 0, total: N}` | null | null |
| `processing` | 正在解析 | `{current: M, total: N}` | null | null |
| `completed` | 全部完成 | `{current: N, total: N}` | null | null |
| `incomplete` | 部分完成 | `{current: M, total: N}` 其中 M < N | 有值 | null |
| `failed` | 完全失败 | `{current: 0, total: N}` | 可能有值 | 有值 |

## 新增字段（相对 011 版本）

| 字段 | 说明 |
|------|------|
| `file_name` | 新增。原始文件名，供前端进度条展示 |
| `suggestion` | 新增。Gateway 建议信息透传 |

## 终态行为

- `completed`/`incomplete`/`failed` 为终态，之后不再推送
- 前端收到终态后延迟清除状态（completed: 1.5s, 其他: 3s）
- 轮询超时（超过 max_wait）视为 `failed`，推送一次终态
