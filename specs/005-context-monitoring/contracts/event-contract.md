# Event Contract: context_status (MonitorData)

**传输通道**: Event SSE 流 (GET /api/v1/events/)
**发布方**: AgentService.execute() -> EventService.publish_event()
**消费方**: 前端 useAuth.tsx -> window.CustomEvent('context-status') -> ContextStatusBar + MonitorSidebar

## 事件格式

### SSE Wire Format

```
event: message
data: {"type":"context_status","model_name":"deepseek-v3-1-terminus","total_tokens":1844,"input_tokens":784,"output_tokens":892,"max_context_tokens":65536,"pct":73.0,"alert":"warning","breakdown":{"system_prompt":1200,"history":35000,"memories":3500,"compaction":0,"tool_defs":800,"tool_calls":200,"tool_results":4000,"tool_count":2,"user_input":500,"total":45200},"memory_types":[{"tag":"个人喜好","tokens":350},{"tag":"职业信息","tokens":480},{"tag":"工作任务","tokens":260}],"memory_count":34,"memory_records":[{"id":1,"content":"我叫安琳...","tag":"职业信息","updated_at":"2026-02-03T10:00:00Z","token_count":120}],"tool_processes":[{"name":"Brave Search API","task":"搜索","input_tokens":120,"output_tokens":450}],"request_id":"req_abc123"}

```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | Y | 固定为 `"context_status"` |
| model_name | string | Y | 当前模型名称 |
| total_tokens | int | Y | 本次请求累计总 token 数（输入+输出） |
| input_tokens | int | Y | 累计输入 token 数 |
| output_tokens | int | Y | 累计输出 token 数 |
| max_context_tokens | int | Y | 模型最大上下文窗口（完整值，非 90%） |
| pct | float | Y | 上下文使用百分比 (0.0-100.0) |
| alert | string | Y | 告警级别: `"normal"` / `"warning"` / `"critical"` |
| breakdown | object | Y | 上下文 token 分部详情 |
| memory_types | array | Y | 记忆语义标签 token 占比列表（按 UserMemory.tags[0] 分组） |
| memory_count | int | Y | 记忆条目总数（显示为"总计: XX 条"） |
| memory_records | array | Y | 前 4 条记忆记录（按 updated_at 倒序） |
| tool_processes | array | Y | 本轮实际工具调用记录（按 output_tokens 倒序） |
| request_id | string | N | 关联的请求 ID |

### breakdown 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| system_prompt | int | 系统提示词 |
| history | int | 对话历史 |
| memories | int | 召回记忆 |
| compaction | int | 压缩摘要 |
| tool_defs | int | 工具定义 |
| tool_calls | int | 工具调用指令 |
| tool_results | int | 工具返回结果 |
| tool_count | int | 工具调用次数 |
| user_input | int | 用户输入 |
| total | int | 所有部分之和 |

### memory_types 数组元素

| 字段 | 类型 | 说明 |
|------|------|------|
| tag | string | 语义标签（如 "个人喜好"、"职业信息"、"工作任务"、"日常对话"），来源于 UserMemory.tags[0]，tags 为空时为"未分类" |
| tokens | int | 该标签所有记忆条目的 token 总数 |

### memory_records 数组元素

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 记忆 ID |
| content | string | 记忆内容（可截断） |
| tag | string | 语义标签（UserMemory.tags[0]），tags 为空时为"未分类" |
| updated_at | string | 更新时间 (ISO 8601) |
| token_count | int | 该条记忆的 token 数 |

### tool_processes 数组元素

| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 工具名称 |
| task | string | 任务描述 |
| input_tokens | int | 输入 token 数 |
| output_tokens | int | 输出 token 数 |

## 发布时机

1. **每次用户发消息时**: 上下文构建完成后推送一次（含 normal 级别），tool_processes 为空数组
2. **Agent 流式响应期间**: 每 500ms 推送一次完整 MonitorData 快照（含最新的 token 累计、工具调用等）
3. **告警级别变化时**: 立即额外推送一次（不等待 500ms 周期）

## 消费行为

### ContextStatusBar（输入框下方）
- `alert == "normal"`: 不显示，不占用空间
- `alert == "warning"`: 显示蓝色状态条 + "上下文: XX%" 进度条 + "超过70%将会自动压缩会话"
- `alert == "critical"`: 显示红色状态条 + "上下文: XX%" 进度条 + "建议开始新对话"

### MonitorSidebar（右侧侧边栏，默认收起）
- 大模型输入输出: 更新 model_name、tokens/输入/输出 数值、input_tokens/output_tokens 折线图（维护最近 60 个数据点）
- 当前上下文: 更新 breakdown 堆叠柱状图 + "最大值: XX tokens"
- 当前记忆: 更新 memory_types 语义标签 token 占比（按 UserMemory.tags[0] 分组）+ "总计: XX 条"（memory_count）+ memory_records 列表
- 当前进程: 更新 tool_processes 列表（初始为空，有工具调用后出现）
