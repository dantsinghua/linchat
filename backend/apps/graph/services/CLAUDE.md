# graph/services 指南

> Agent 执行服务包，封装 LangGraph Agent 的完整执行生命周期。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `__init__.py` | 导出 `AgentService` |
| `agent_service.py` | Agent 执行入口（流式 SSE）、继续生成、取消信号监听 |

---

## AgentService

### `execute(user_id, thread_id, request_id, user_message, attachment_uuids=None)`

完整的 Agent 执行流程：

1. **多模态检测**: 有 `attachment_uuids` → 加载附件 → 检查过期 → 注册推理任务
2. **LangGraphExecution 入库**: status=pending → running
3. **构建 Prompt**: `_build_prompt_preamble()` — 记忆召回 + 历史裁剪 + PromptBuilder
4. **选择 Agent**:
   - 多模态 → `create_multimodal_direct()` (httpx 直连 Gateway)
   - 纯文本 → `create_chat_agent()` (LangChain + LangGraph)
5. **流式执行**: `astream_events(version="v2")` → 逐块 yield `StreamChunk`
6. **首 token 入库**: 创建 user + assistant Message
7. **监控推送**: 500ms 间隔推送 `context_status` SSE 事件
8. **完成/中断/失败**: 更新 Message 状态 + Execution 记录

### `resume(user_id, thread_id, request_id, message)`

恢复中断的生成，在已有 assistant 消息基础上继续输出。

---

## 内部辅助函数

| 函数 | 说明 |
|------|------|
| `_build_prompt_preamble()` | 构建 Agent 前置消息列表（System Prompt + 记忆 + 历史） |
| `_monitor_cancel_signal()` | Redis Pub/Sub 监听推理取消信号（降级轮询） |
| `_poll_cancel_signal()` | 降级轮询 Redis inference_task 键 |
| `_init_langfuse()` | 初始化 Langfuse 追踪（含多模态元数据注入） |
| `_extract_usage()` | 从 LLM 输出提取 token 用量 |
| `_extract_gateway_error()` | 解析 Gateway E3001/E3002 错误码 |
| `_extract_content_control()` | 检测 Gateway content_control 安全护栏事件 |
| `_finalize_message()` / `_finalize_execution()` | 设置最终状态字段 |

---

## SubAgent 输出过滤

`astream_events` 中通过 `parent_ids` 深度判断：
- `len(parent_ids) <= 3` → 主 Agent 输出，推送到 SSE
- `len(parent_ids) > 3` → SubAgent 内部 LLM 输出，跳过

---

## 关键导入路径

```python
from apps.graph.services import AgentService
```

## 测试 patch 路径

```python
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.graph.services.agent_service._build_prompt_preamble")
@patch("apps.graph.services.agent_service._init_langfuse")
```
