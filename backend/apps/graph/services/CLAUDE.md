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

1. **多模态检测**: 有 `attachment_uuids` -> 加载附件 -> 检查过期 -> 提取 media_types -> 注册推理任务（并发控制）
2. **LangGraphExecution 入库**: status=pending -> running
3. **取消信号监听**: 多模态请求启动后台 `_monitor_cancel_signal` 任务
4. **Langfuse 初始化**: `_init_langfuse()`（多模态推理包含 model/media_types/attachment_count 元数据）
5. **构建 Prompt**: `_build_prompt_preamble()` -> 记忆召回 + 历史裁剪 + PromptBuilder
6. **监控初始化**: `ContextMonitor.build_monitor_data()` -> 推送 `context_status` SSE 事件
7. **上下文压缩检测**: `ContextService.check_token_limit()` -> 触发时先压缩再继续
8. **统一使用 `create_chat_agent()`**: 多模态由 SubAgent 内部处理（`multimodal_subagent`），主 Agent 始终使用工具模型
9. **流式执行**: `astream_events(version="v2")` -> 逐块 yield `StreamChunk`
10. **首 token 入库**: 创建 user + assistant Message（多模态时关联附件到用户消息）
11. **监控推送**: 500ms 间隔推送 `context_status` SSE 事件（含 token 用量和工具调用追踪）
12. **完成处理**: 更新 Message/Execution 状态 + 用户统计（消息数/token 数）+ 最终监控推送

**输入消息构建**: 多模态时在 user_message 后附加附件描述（`[用户上传了 N 个附件: ...]`），含文档附件时使用 `AGENT_MULTIMODAL_TIMEOUT`（1500s）。

### `resume(user_id, thread_id, request_id, message)`

恢复中断的生成：
- 在已有 assistant 消息基础上继续输出
- 发送 `HumanMessage("请继续")` 触发 Agent 继续生成
- SubAgent 内部 LLM 输出同样通过 `parent_ids` 深度过滤

---

## 内部辅助函数

| 函数 | 说明 |
|------|------|
| `_build_prompt_preamble()` | 构建 Agent 前置消息列表：获取模型配置 -> 记忆召回（MemoryService.search_memory）-> DB 拉取对话历史 -> token 预算裁剪 -> PromptBuilder.build_preamble_with_breakdown |
| `_monitor_cancel_signal()` | Redis Pub/Sub 监听 `INFERENCE_CANCEL` 事件，解析 SSE 格式中的 `data` 行，匹配 request_id 后设置 stop_event |
| `_poll_cancel_signal()` | Pub/Sub 失败时降级：轮询 Redis `user:{user_id}:inference_task` 键，键不存在即视为取消（1s 间隔） |
| `_init_langfuse()` | 初始化 Langfuse 追踪，设置环境变量 + trace_context；多模态推理注入 metadata 和 tags |
| `_extract_usage()` | 从 LLM 输出提取 token 用量：优先 `usage_metadata`，降级 `response_metadata.token_usage` |
| `_extract_gateway_error()` | 解析 Gateway 错误码：E3001（模型不存在）、E3002（服务不可用，含 retry_after） |
| `_extract_content_control()` | 检测 Gateway `content_control` 安全护栏事件，提取 replacement 文本 |
| `_finalize_message()` | 设置 Message 最终字段（content, status, response_time_ms, token 用量） |
| `_finalize_execution()` | 设置 Execution 最终字段（status, end_time, duration_ms, output_data, token 用量, langfuse_trace_id, error） |

---

## SubAgent 输出过滤

`astream_events` 中通过 `parent_ids` 深度判断：
- `len(parent_ids) <= 3` -> 主 Agent 输出，推送到 SSE
- `len(parent_ids) > 3` -> SubAgent 内部 LLM 输出，跳过（不暴露给用户）

---

## 异常处理流程

1. **LLMException**: 直接 raise（由视图层处理）
2. **content_control**: Gateway 安全护栏触发 -> yield error StreamChunk（`content_control: True`）
3. **Gateway E3001/E3002**: 模型错误 -> yield error StreamChunk（含 `gateway_error` 和可选 `retry_after`）
4. **其他异常**: `map_llm_exception(e)` 转换后 raise
5. **finally**: 清理取消监听任务 + 完成推理任务（清理 Redis 键）+ Langfuse flush

---

## 监控数据推送 [005-context-monitoring]

- 初始化时推送一次 `context_status`
- 流式执行期间 500ms 间隔定时推送（含 token 用量增量更新）
- 告警级别变化时立即推送
- Agent 完成后推送最终数据（含正确的 token 用量；memory_modified 时重新搜索记忆）
- 工具调用追踪：`on_tool_end` 事件中记录工具名、输入/输出 token 数

---

## 关键导入路径

```python
from apps.graph.services import AgentService
```

## 测试 patch 路径

```python
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.graph.services.agent_service.AgentService.resume")
@patch("apps.graph.services.agent_service._build_prompt_preamble")
@patch("apps.graph.services.agent_service._init_langfuse")
@patch("apps.graph.services.agent_service._monitor_cancel_signal")
@patch("apps.graph.services.agent_service._extract_gateway_error")
@patch("apps.graph.services.agent_service._extract_content_control")
```

---

## 注意事项

1. `execute` 统一使用 `create_chat_agent`（工具模型），多模态推理由 `multimodal_subagent` 内部处理
2. `attachment_uuids`、`stop_event`、`request_id` 通过 `config["configurable"]` 传递到 SubAgent
3. 多模态请求需要注册推理任务（`inference_service.register_task`），完成后清理
4. `_build_prompt_preamble` 返回 7 元组：`(preamble, preamble_tokens, effective_window, breakdown, memory_results, model_name, max_context_window)`
5. 首 token 收到时才创建 Message 记录（避免空响应入库）
6. `memory_subagent` 工具调用结束后标记 `memory_modified`，完成时重新搜索记忆用于最终监控推送
