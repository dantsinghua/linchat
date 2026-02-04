# Data Model: M1c 动态监控

**Feature**: 005-context-monitoring
**Date**: 2026-02-04

## 概述

本特性不引入新的数据库模型。所有数据结构为运行时计算值（Python dataclass），通过 Redis PubSub 推送，不持久化到 PostgreSQL。

## 实体定义

### TokenBreakdown (Python dataclass)

**位置**: `backend/apps/context/types.py`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| system_prompt | int | 0 | 系统提示词 token 数 |
| history_messages | int | 0 | 对话历史 token 数 |
| retrieved_memories | int | 0 | 召回记忆 token 数 |
| compaction_summary | int | 0 | 压缩摘要 token 数 |
| tool_definitions | int | 0 | 工具定义 token 数 |
| user_input | int | 0 | 用户输入 token 数 |
| tool_calls | int | 0 | 工具调用指令 token 数（动态累加） |
| tool_results | int | 0 | 工具返回结果 token 数（动态累加） |
| tool_call_count | int | 0 | 工具调用次数（动态累加） |

**计算属性**:
- `total: int` — 所有字段之和
- `usage_ratio(max_tokens: int) -> float` — 使用率，max_tokens <= 0 时返回 0.0

**序列化方法**:
- `to_dict() -> dict` — 返回扁平字典，键名使用简短别名

**生命周期**:
1. 在 `_build_prompt_preamble()` 中创建，填充静态部分（system_prompt / history / memories / compaction / tool_defs / user_input）。注意：对话历史已从独立的 HumanMessage/AIMessage 序列改为嵌入 `SystemMessage(name="conversation_history")` 的文本块，history_messages 的 token 计数来源于该 SystemMessage 的 content
2. 在 `execute()` 的 `astream_events` 循环中动态累加 tool_calls / tool_results / tool_call_count
3. 请求结束后自动销毁，不持久化

**tool loop 优化**:
- `agent.py` 的 `_wrap_prompt` 在 tool calling 循环中（state 已有 tool 消息时）跳过 `name="conversation_history"` 的 SystemMessage，减少重复 token 消耗约 43-56%
- 此行为不影响 TokenBreakdown 数据：breakdown 记录的是初始 preamble 构建时的完整 token 分布，不随 tool loop 内的 SystemMessage 过滤而变化
- MonitorData 推送的 breakdown 始终反映完整上下文构成，前端可据此准确展示

### AlertLevel (Python Enum)

**位置**: `backend/apps/context/monitoring.py`

| 值 | 阈值 | 日志级别 |
|----|------|----------|
| normal | < 70% | DEBUG |
| warning | 70% - 89% | WARNING |
| critical | >= 90% | ERROR |

### ContextStatus / MonitorData (Event 负载)

**传输方式**: Redis PubSub -> SSE Event 流
**推送频率**: Agent 流式响应期间每 500ms 推送；空闲时仅在用户发消息和告警级别变化时推送

```json
{
  "type": "context_status",
  "model_name": "deepseek-v3-1-terminus",
  "total_tokens": 1844,
  "input_tokens": 784,
  "output_tokens": 892,
  "max_context_tokens": 65536,
  "pct": 73.0,
  "alert": "warning",
  "breakdown": {
    "system_prompt": 1200,
    "history": 35000,
    "memories": 3500,
    "compaction": 0,
    "tool_defs": 800,
    "tool_calls": 200,
    "tool_results": 4000,
    "tool_count": 2,
    "user_input": 500,
    "total": 45200
  },
  "memory_types": [
    { "tag": "个人喜好", "tokens": 350 },
    { "tag": "职业信息", "tokens": 480 },
    { "tag": "工作任务", "tokens": 260 },
    { "tag": "日常对话", "tokens": 150 }
  ],
  "memory_count": 34,
  "memory_records": [
    {
      "id": 1,
      "content": "我叫安琳，是一名产品经理...",
      "tag": "职业信息",
      "updated_at": "2026-02-03T10:00:00Z",
      "token_count": 120
    }
  ],
  "tool_processes": [
    {
      "name": "Brave Search API",
      "task": "搜索",
      "input_tokens": 120,
      "output_tokens": 450
    }
  ],
  "request_id": "req_abc123"
}
```

### 前端类型定义

**位置**: `frontend/src/types/index.ts`

```typescript
interface TokenBreakdown {
  system_prompt: number;
  history: number;
  memories: number;
  compaction: number;
  tool_defs: number;
  tool_calls: number;
  tool_results: number;
  tool_count: number;
  user_input: number;
  total: number;
}

type AlertLevel = 'normal' | 'warning' | 'critical';

interface MemoryRecord {
  id: number;
  content: string;
  tag: string;         // UserMemory.tags[0] 语义标签（如"个人喜好"/"职业信息"），tags 为空时为"未分类"
  updated_at: string;
  token_count: number;
}

interface ToolProcess {
  name: string;
  task: string;
  input_tokens: number;
  output_tokens: number;
}

interface MonitorData {
  // 大模型输入输出
  model_name: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  // 当前上下文
  context_breakdown: TokenBreakdown;
  max_context_tokens: number;
  alert: AlertLevel;
  pct: number;
  // 当前记忆
  memory_types: { tag: string; tokens: number }[];   // 按 UserMemory.tags[0] 分组，tag 为语义标签，tokens 为该标签所有记忆的 token 总数
  memory_count: number;                               // 记忆条目总数（显示为"总计: XX 条"）
  memory_records: MemoryRecord[];                     // 前 4 条记忆，按 updated_at 倒序
  // 当前进程
  tool_processes: ToolProcess[];
}

interface ContextStatus extends MonitorData {
  type: 'context_status';
  request_id?: string;
}
```

## 状态转换

本特性无数据库状态转换。Embedding 健康检查操作现有 UserMemory 模型的 `embedding_status` 字段（pending / processing / done / failed），但不改变其状态机定义。
