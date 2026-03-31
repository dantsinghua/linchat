# graph/services 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。所有隔离按 user_id 粒度。

Agent 执行服务包，封装 LangGraph Agent 的完整执行生命周期。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `__init__.py` | 统一导出 AgentService, ContextService, GPULockTimeout, InferenceService, acquire_gpu_lock, inference_service |
| `agent_service.py` | Agent 执行入口 (`execute`/`resume`)，流式 SSE 输出 |
| `agent_helpers.py` | 向后兼容 re-export 层；逻辑已迁移到 `helpers/`，保留 `finalize_interrupted`/`push_monitor_update`/`check_context_compression`/`compress_context` 等遗留适配函数 |
| `helpers/` | Agent 辅助函数包（从 agent_helpers.py 拆分为 4 个子模块，见下表） |
| `cancel_monitor.py` | 推理取消信号监听：Redis Pub/Sub 优先，降级为轮询 |
| `context_service.py` | 上下文窗口管理：token 限额检查、三级压缩、LLM 摘要、完整上下文构建 |
| `gpu_lock.py` | GPU 互斥锁：Redis 分布式锁 + 心跳续期，避免多模态推理显存冲突 |
| `inference_service.py` | 推理任务管理：注册/完成/取消/TTL 刷新，Redis 键 `user:{user_id}:inference_task` |

### helpers/ 包

| 文件 | 职责 | 核心函数 |
|------|------|----------|
| `__init__.py` | 统一导出所有公共符号 | — |
| `prompt.py` | Prompt 构建 + 记忆召回 + 历史裁剪 | `build_prompt_preamble()` |
| `errors.py` | LLM 输出解析 + 错误提取 | `extract_usage()`, `extract_gateway_error()`, `extract_content_control()` |
| `finalize.py` | 消息/执行记录收尾 + 首 token 消息创建 | `finalize_message()`, `finalize_execution()`, `finalize_completion()`, `handle_execution_failure()`, `create_first_token_messages()` |
| `monitor.py` | Langfuse 初始化 + 监控推送 + 工具事件处理 | `init_langfuse()`, `publish_monitor()`, `init_monitor_data()`, `handle_tool_end_event()`, `push_final_monitor()` |

---

## 核心函数速查

### helpers/prompt.py

| 函数 | 说明 |
|------|------|
| `build_prompt_preamble(user_id, user_message)` | 记忆召回 + DB 历史 + token 预算裁剪 + PromptBuilder，返回 7 元组 `(preamble, preamble_tokens, effective_window, breakdown, memory_results, model_name, max_context_window)` |

### helpers/errors.py

| 函数 | 说明 |
|------|------|
| `extract_usage(output)` | 从 LLM 输出提取 token 用量（优先 `usage_metadata`，降级 `response_metadata`） |
| `extract_gateway_error(e)` | 解析 Gateway 错误码 E3001（模型不存在）/ E3002（服务不可用） |
| `extract_content_control(e)` | 检测安全护栏事件，从 data/body/response 多来源提取 replacement 文本 |

### helpers/finalize.py

| 函数 | 说明 |
|------|------|
| `finalize_message(msg, ...)` | 设置消息 content/status/response_time_ms/tokens |
| `finalize_execution(ex, ...)` | 设置执行记录状态/耗时/token/langfuse_trace_id/错误信息 |
| `finalize_completion(execution, ..., interrupted=False)` | 成功/中断收尾：更新 Message + Execution + 用户统计 |
| `handle_execution_failure(execution, ...)` | 失败收尾：更新 Execution 和可选 assistant 消息（status 用整数 `0`） |
| `create_first_token_messages(user_id, ...)` | 首 token 时创建 user + assistant Message，多模态时关联附件 |

### helpers/monitor.py

| 函数 | 说明 |
|------|------|
| `init_langfuse(request_id, multimodal_metadata)` | 初始化 Langfuse `CallbackHandler`（`trace_context={"trace_id": request_id}`） |
| `publish_monitor(breakdown, ...)` | 构建并推送 `context_status` SSE 事件 |
| `init_monitor_data(breakdown, ...)` | 初始化并推送首次 `context_status` |
| `handle_tool_end_event(event, breakdown, tool_processes)` | 记录工具调用 token，检测 `memory_subagent` 返回 True |
| `push_final_monitor(user_id, ...)` | 推送最终监控（`memory_modified` 时重新搜索记忆） |

---

## AgentService (agent_service.py)

### `execute(user_id, thread_id, request_id, user_message, attachment_uuids=None)`

1. 多模态检测 → 加载附件 → 注册推理任务（并发控制）
2. LangGraphExecution 入库（pending → running），多模态启动 `monitor_cancel_signal`
3. Langfuse 初始化 + `build_prompt_preamble()`（记忆召回 + 历史裁剪）
4. 上下文压缩检测 → 触发时先压缩（发送 context_compacting/compacted 事件）
5. `create_chat_agent()` + `astream_events(v2)` 流式执行
6. 首 token 时创建 user + assistant Message，500ms 间隔推送 `context_status` 监控
7. 完成后更新 Message/Execution + 用户统计；多模态文档超时 `AGENT_MULTIMODAL_TIMEOUT`（1500s）

### `resume(user_id, thread_id, request_id, message)`

恢复中断的生成，去除 `[已中断]` 标记后追加新内容。

---

## cancel_monitor.py

| 函数 | 说明 |
|------|------|
| `monitor_cancel_signal()` | Redis Pub/Sub 监听 `inference_cancel` 事件，失败降级为 `poll_cancel_signal` |
| `poll_cancel_signal()` | 轮询 Redis 键是否存在（1s 间隔） |

---

## ContextService (context_service.py)

三级压缩策略（Redis 分布式锁 `compress:{user_id}`，60s 超时）：

| 级别 | 目标 | 策略 |
|------|------|------|
| L1 | 对话历史（排除最后一条 user） | LLM 摘要压缩 |
| L2 | 工具结果（name="tools"） | 直接丢弃 |
| L3 | 记忆/摘要（name="memory"/"compaction"） | 直接丢弃 |

| 方法 | 说明 |
|------|------|
| `get_effective_window()` | max_context_window * 0.9，最小 10000 |
| `check_token_limit()` | 消息总 token 是否超过 effective_window |
| `compress_context()` | 三级压缩（Redis 锁 + LLM 摘要 + trim），摘要存为 `compaction` Memory |
| `build_context()` | 完整上下文构建：记忆召回 + PromptBuilder + 自动压缩 |

---

## GPULock (gpu_lock.py)

`acquire_gpu_lock(request_id)` — 异步上下文管理器，Redis 键 `multimodal:gpu_lock`：TTL 60s + 30s 心跳续期，支持重入，最长等待 600s，3s 轮询。

---

## InferenceService (inference_service.py)

Redis 键 `user:{user_id}:inference_task`，`InferenceTask` 数据类序列化。模块级单例 `inference_service`。

| 方法 | 说明 |
|------|------|
| `register_task()` | NX 写入，TTL 300s |
| `complete_task()` | 验证 request_id 后删除 |
| `cancel_task()` | 删除键 + `signal_stop` + Pub/Sub `inference_cancel` |
| `get_active_task()` | 查询当前任务 |
| `refresh_task_ttl()` | 续期 TTL（文档解析期间） |

---

## 注意事项

1. `execute` 统一使用 `create_chat_agent`，多模态推理由 `multimodal_subagent` 内部处理
2. SubAgent 内部 LLM 输出通过 `parent_ids` 深度 > 3 过滤，不推送到 SSE
3. 首 token 收到时才创建 Message 记录（避免空响应入库）
4. `memory_subagent` 调用后标记 `memory_modified`，完成时重新搜索记忆
5. `cancel_task` 同时做两件事：`signal_stop` 停止本地 generation + Pub/Sub 通知远程监控
6. `handle_execution_failure` 中 `assistant_msg.status` 必须用整数 `0`，不可用字符串


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1044 | 11:00 AM | ⚖️ | Code Review Findings for Multimodal Feature Require Comprehensive Fix Plan | ~728 |

### Mar 11, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1628 | 8:33 AM | 🔵 | Model Configuration Consumption in Graph Agents | ~380 |

### Mar 30, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #2033 | 3:05 PM | 🔵 | DeerFlow vs LinChat Agent Architecture Comparison | ~580 |
</claude-mem-context>