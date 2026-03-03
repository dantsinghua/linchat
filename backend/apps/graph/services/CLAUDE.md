# graph/services 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

Agent 执行服务包，封装 LangGraph Agent 的完整执行生命周期。graph 是 LangGraph Agent Pipeline 的核心。

---

## 文件结构

| 文件 | 职责 | 来源 |
|------|------|------|
| `__init__.py` | 统一导出 AgentService, ContextService, GPULockTimeout, InferenceService, acquire_gpu_lock, inference_service | 原有 |
| `agent_service.py` | Agent 执行入口 (`execute`/`resume`)，流式 SSE 输出 | 原有（大幅瘦身） |
| `agent_helpers.py` | Agent 辅助函数：prompt 构建、Langfuse 初始化、token 提取、监控推送、执行收尾 | 从 agent_service.py 拆分 |
| `cancel_monitor.py` | 推理取消信号监听：Redis Pub/Sub 优先，降级为轮询 | 从 agent_service.py 拆分 |
| `context_service.py` | 上下文窗口管理：token 限额检查、三级压缩、LLM 摘要、完整上下文构建 | 从 `chat/services` 迁移 |
| `gpu_lock.py` | GPU 互斥锁：Redis 分布式锁 + 心跳续期，避免多模态推理显存冲突 | 从 `chat/services` 迁移 |
| `inference_service.py` | 推理任务管理：注册/完成/取消/TTL 刷新，Redis 键 `user:{user_id}:inference_task` | 从 `chat/services` 迁移 |

---

## AgentService (agent_service.py)

### `execute(user_id, thread_id, request_id, user_message, attachment_uuids=None)`

1. 多模态检测 -> 加载附件 -> 注册推理任务（并发控制）
2. LangGraphExecution 入库（pending -> running）
3. 多模态请求启动 `monitor_cancel_signal` 后台任务
4. Langfuse 初始化 + Prompt 构建（记忆召回 + 历史裁剪）
5. 上下文压缩检测 -> 触发时先压缩（发送 context_compacting/compacted 事件）
6. `create_chat_agent()` + `astream_events(v2)` 流式执行
7. 首 token 时创建 user+assistant Message（多模态时关联附件到 user 消息）
8. 500ms 间隔推送 `context_status` 监控事件（`MONITOR_PUSH_INTERVAL` 可配置）
9. 完成后更新 Message/Execution + 用户统计（消息数 +2、token 累加）
10. 多模态文档超时使用 `AGENT_MULTIMODAL_TIMEOUT`（1500s），其他使用 `AGENT_TOTAL_TIMEOUT`

### `resume(user_id, thread_id, request_id, message)`

恢复中断的生成，在已有 assistant 消息基础上继续输出。去除 `[已中断]` 标记后追加新内容。

---

## agent_helpers.py 关键函数

| 函数 | 说明 |
|------|------|
| `build_prompt_preamble()` | 记忆召回 + DB 拉取历史 + token 预算裁剪 + PromptBuilder，返回 7 元组 (preamble, preamble_tokens, effective_window, breakdown, memory_results, model_name, max_context_window) |
| `init_langfuse()` | 初始化 Langfuse CallbackHandler（`trace_context={"trace_id": request_id}`） |
| `extract_usage()` | 从 LLM 输出提取 token 用量（优先 usage_metadata，降级 response_metadata） |
| `extract_gateway_error()` | 解析 Gateway 错误码 E3001（模型不存在）/E3002（服务不可用） |
| `extract_content_control()` | 检测安全护栏事件，从 data/body/response 多来源提取 replacement 文本 |
| `handle_tool_end_event()` | 记录工具调用 token 到 breakdown，检测 memory_subagent 调用返回 True |
| `create_first_token_messages()` | 创建 user+assistant Message（多模态时关联附件到 user 消息） |
| `finalize_message()` | 更新消息 content/status/response_time_ms/tokens |
| `finalize_execution()` | 更新执行记录最终状态（含 langfuse_trace_id） |
| `finalize_success()` | 成功收尾：更新 Message + Execution + 用户统计 |
| `finalize_interrupted()` | 中断收尾：追加 `[已中断]` 标记 |
| `handle_execution_failure()` | 失败收尾：更新 Execution 和可选的 assistant 消息 |
| `check_context_compression()` | 检查上下文是否需要压缩 |
| `compress_context()` | 调用 ContextService 执行压缩 |
| `init_monitor_data()` | 初始化监控数据并推送首次 context_status |
| `push_monitor_update()` | 推送周期性 context_status SSE 事件 |
| `push_final_monitor()` | 推送最终监控（memory_modified 时重新搜索记忆） |
| `validate_attachments()` | 校验附件有效性（用于外部调用） |

---

## cancel_monitor.py

| 函数 | 说明 |
|------|------|
| `monitor_cancel_signal()` | Redis Pub/Sub 监听用户事件频道，匹配 `inference_cancel` 事件后设置 stop_event；失败降级为 `poll_cancel_signal` |
| `poll_cancel_signal()` | 降级方案：轮询 Redis 键 `user:{user_id}:inference_task` 是否存在（1s 间隔） |

---

## ContextService (context_service.py) -- 从 chat 迁移

### 三级压缩

| 级别 | 目标 | 策略 |
|------|------|------|
| L1 (TrimLevel.FIRST) | 对话历史（user/assistant，排除最后一条 user） | LLM 摘要压缩 |
| L2 (TrimLevel.SECOND) | 工具结果（name="tools"） | 直接丢弃 |
| L3 (TrimLevel.LAST) | 记忆/摘要（name="memory"/"compaction"） | 直接丢弃 |

压缩过程使用 Redis 分布式锁（`compress:{user_id}`，60s 超时），避免并发压缩。
压缩后的摘要作为 `compaction` 类型 Memory 保存。

### 关键方法

| 方法 | 说明 |
|------|------|
| `get_effective_window()` | 模型 max_context_window * 0.9，最小 10000 |
| `check_token_limit()` | 消息总 token 是否超过 effective_window |
| `compress_context()` | 执行三级压缩（含 Redis 锁 + LLM 摘要 + 最终 trim） |
| `build_context()` | 完整上下文构建：记忆召回 + PromptBuilder + 自动压缩 |

---

## GPULock (gpu_lock.py) -- 从 chat 迁移

`acquire_gpu_lock(request_id)` -- 异步上下文管理器，Redis 键 `multimodal:gpu_lock`：
- TTL 60s + 30s 心跳续期（后台 `_heartbeat` 任务）
- 支持重入（同一 request_id）
- 最长等待 600s（可配置 `GPU_LOCK_MAX_WAIT`）
- 3s 轮询间隔检查锁状态

---

## InferenceService (inference_service.py) -- 从 chat 迁移

Redis 键 `user:{user_id}:inference_task` 管理推理任务生命周期，使用 `InferenceTask` 数据类序列化：

| 方法 | 说明 |
|------|------|
| `register_task()` | NX 写入任务信息，TTL 300s（`INFERENCE_TASK_TTL`） |
| `complete_task()` | 验证 request_id 后删除 |
| `cancel_task()` | 删除键 + `signal_stop` + 发布 Pub/Sub `inference_cancel` 事件 |
| `get_active_task()` | 查询当前任务，返回 `InferenceTask` 或 None |
| `refresh_task_ttl()` | 续期 TTL（文档解析轮询期间使用） |

模块级单例：`inference_service = InferenceService()`

---

## 注意事项

1. `execute` 统一使用 `create_chat_agent`，多模态推理由 `multimodal_subagent` 内部处理
2. SubAgent 内部 LLM 输出通过 `parent_ids` 深度 > 3 过滤，不推送到 SSE
3. 首 token 收到时才创建 Message 记录（避免空响应入库）
4. `memory_subagent` 调用后标记 `memory_modified`，完成时重新搜索记忆
5. `cancel_task` 同时做两件事：`signal_stop` 停止本地 generation + Pub/Sub 通知远程监控