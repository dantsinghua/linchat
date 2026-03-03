# graph/services 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

Agent 执行服务包，封装 LangGraph Agent 的完整执行生命周期。graph 是 LangGraph Agent Pipeline 的核心。

---

## 文件结构

| 文件 | 职责 | 来源 |
|------|------|------|
| `__init__.py` | 统一导出 AgentService, ContextService, GPULockTimeout, InferenceService 等 | 原有 |
| `agent_service.py` | Agent 执行入口 (`execute`/`resume`)，流式 SSE 输出 | 原有 |
| `agent_helpers.py` | Agent 辅助函数：prompt 构建、Langfuse 初始化、token 提取、监控推送、执行收尾 | 从 agent_service.py 拆分 |
| `cancel_monitor.py` | 推理取消信号监听：Redis Pub/Sub 优先，降级为轮询 | 从 agent_service.py 拆分 |
| `context_service.py` | 上下文窗口管理：token 限额检查、三级压缩、LLM 摘要 | 从 `chat/services` 迁移 |
| `gpu_lock.py` | GPU 互斥锁：Redis 分布式锁 + 心跳续期，避免多模态推理显存冲突 | 从 `chat/services` 迁移 |
| `inference_service.py` | 推理任务管理：注册/完成/取消/TTL 刷新，Redis 键 `user:{user_id}:inference_task` | 从 `chat/services` 迁移 |

---

## AgentService (agent_service.py)

### `execute(user_id, thread_id, request_id, user_message, attachment_uuids=None)`

1. 多模态检测 -> 加载附件 -> 注册推理任务（并发控制）
2. LangGraphExecution 入库（pending -> running）
3. 多模态请求启动 `monitor_cancel_signal` 后台任务
4. Langfuse 初始化 + Prompt 构建（记忆召回 + 历史裁剪）
5. 上下文压缩检测 -> 触发时先压缩
6. `create_chat_agent()` + `astream_events(v2)` 流式执行
7. 首 token 时创建 user+assistant Message
8. 500ms 间隔推送 `context_status` 监控事件
9. 完成后更新 Message/Execution + 用户统计

### `resume(user_id, thread_id, request_id, message)`

恢复中断的生成，在已有 assistant 消息基础上继续输出。

---

## agent_helpers.py 关键函数

| 函数 | 说明 |
|------|------|
| `build_prompt_preamble()` | 记忆召回 + DB 拉取历史 + token 预算裁剪 + PromptBuilder，返回 7 元组 |
| `init_langfuse()` | 初始化 Langfuse CallbackHandler（trace_context） |
| `extract_usage()` | 从 LLM 输出提取 token 用量（优先 usage_metadata） |
| `extract_gateway_error()` | 解析 Gateway 错误码 E3001/E3002 |
| `extract_content_control()` | 检测安全护栏事件，提取 replacement 文本 |
| `handle_tool_end_event()` | 记录工具调用 token，检测 memory_subagent 调用 |
| `create_first_token_messages()` | 创建 user+assistant Message（多模态时关联附件） |
| `finalize_success/interrupted()` | 更新 Message/Execution 最终状态 |
| `push_monitor_update/push_final_monitor()` | 推送 context_status SSE 事件 |

---

## cancel_monitor.py

| 函数 | 说明 |
|------|------|
| `monitor_cancel_signal()` | Redis Pub/Sub 监听 `inference_cancel` 事件，匹配 request_id 后设置 stop_event |
| `poll_cancel_signal()` | 降级方案：轮询 Redis 键是否存在（1s 间隔） |

---

## ContextService (context_service.py) -- 从 chat 迁移

三级上下文压缩：L1（对话历史，LLM 摘要）-> L2（工具结果，直接丢弃）-> L3（记忆/摘要，丢弃）。
压缩过程使用 Redis 分布式锁（`compress:{user_id}`），避免并发压缩。

---

## GPULock (gpu_lock.py) -- 从 chat 迁移

`acquire_gpu_lock(request_id)` -- 异步上下文管理器，Redis 键 `multimodal:gpu_lock`：
- TTL 60s + 30s 心跳续期
- 支持重入（同一 request_id）
- 最长等待 600s（可配置 `GPU_LOCK_MAX_WAIT`）

---

## InferenceService (inference_service.py) -- 从 chat 迁移

Redis 键 `user:{user_id}:inference_task` 管理推理任务生命周期：

| 方法 | 说明 |
|------|------|
| `register_task()` | NX 写入任务信息，TTL 300s |
| `complete_task()` | 验证 request_id 后删除 |
| `cancel_task()` | 删除键 + signal_stop + 发布 Pub/Sub 取消事件 |
| `get_active_task()` | 查询当前任务 |
| `refresh_task_ttl()` | 续期 TTL（文档解析轮询期间使用） |

---

## 注意事项

1. `execute` 统一使用 `create_chat_agent`，多模态推理由 `multimodal_subagent` 内部处理
2. SubAgent 内部 LLM 输出通过 `parent_ids` 深度 > 3 过滤，不推送到 SSE
3. 首 token 收到时才创建 Message 记录（避免空响应入库）
4. `memory_subagent` 调用后标记 `memory_modified`，完成时重新搜索记忆


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1044 | 11:00 AM | ⚖️ | Code Review Findings for Multimodal Feature Require Comprehensive Fix Plan | ~728 |
</claude-mem-context>