# Research: M1c 动态监控

**Feature**: 005-context-monitoring
**Date**: 2026-02-04

## R1: EventService 事件推送可靠性

**Decision**: 复用现有 EventService + Redis PubSub 推送 context_status 事件

**Rationale**:
- 现有 `EventService.subscribe_user_events()` 已实现完整的 Redis PubSub 订阅循环，含 30s 心跳和自动重连
- `SSEEvent` dataclass 已支持任意 `data: dict` 负载，无需扩展
- `publish_logout_event()` 的模式可直接复用为通用 `publish_event()` 方法
- Redis PubSub 的延迟通常 < 1ms，满足 100ms 额外延迟目标

**Alternatives considered**:
- 新建独立 EventBus 模块：过度设计，现有 EventService 已是功能完整的事件总线
- 通过 Chat 流发送监控事件：违反 FR-010（Chat 流 6 种 type 不得改变），且增加前端解析复杂度
- WebSocket 推送：现有项目 WebSocket 未启用，引入成本高

## R2: Token 计数性能影响

**Decision**: 在 Agent 执行热路径中同步调用 `count_tokens()` 计算 breakdown

**Rationale**:
- 现有 `_build_prompt_preamble()` 已同步计算 `fixed_tokens` 和 `preamble_tokens`（代码已重构，历史改为 SystemMessage 文本块）
- tiktoken 的 `count_tokens()` 对于 < 100KB 文本，耗时通常 < 5ms
- breakdown 中各部分的文本在 `build_preamble()` 过程中已构建，额外计数仅需分别调用 `count_tokens()`
- 异步化 token 计数无明显收益（CPU-bound 操作，GIL 下异步不加速）

**Alternatives considered**:
- 异步 token 计数：CPU-bound 操作异步化无意义，反而增加代码复杂度
- 基于字符数估算 token：精度不够，无法支持准确的阈值告警
- 缓存 token 计数：preamble 每次构建都不同（含动态记忆/历史），缓存命中率低

## R3: 前端跨组件通信方式

**Decision**: 使用 `window.CustomEvent` 从 `useAuth.tsx` 分发 context_status 到聊天页组件

**Rationale**:
- `useAuth.tsx` 在全局 layout 层处理 Event SSE，context_status 需要传给 `chat/page.tsx` 的组件
- CustomEvent 解耦最简单，不需要改 props 链或引入新的全局 context
- 聊天页组件通过 `addEventListener` 监听，组件卸载时自动清理

**Alternatives considered**:
- Zustand store：需要在 `useAuth` 中引入 chatStore 依赖，产生循环引用风险
- React Context：需要在 layout 层新增 Provider，影响范围大
- Props 传递：需要从 layout 到 chat page 的 props 链，改动多层组件

## R4: 工具结果截断策略

**Decision**: 工具返回结果超过 1500 tokens 时使用 tiktoken 精确计数截断（调用 count_tokens()），不使用字符数近似

**Rationale**:
- 字符估算（1 token ≈ 2 字符）在中英文混合内容中不够精确
- 使用 count_tokens() 精确计数可确保截断后 token 数严格不超过 1500
- 截断后附加 `\n[结果已截断]` 标记，不影响 LLM 理解
- 现有 `python_repl.py` 的 4096 字符截断（行 70-71）是独立逻辑，不受影响

**Alternatives considered**:
- 精确 token 截断（逐步裁减直到 count_tokens <= 1500）：性能差，多次调用 tokenizer
- 按行截断：不够精确，可能截断到远大于或远小于 1500 tokens

## R5: Embedding 健康检查与现有 retry_failed_embeddings 的关系

**Decision**: 新建独立的 `embedding_health_check` 任务，与现有 `retry_failed_embeddings` 互补

**Rationale**:
- 现有 `retry_failed_embeddings` (每 5 分钟) 只处理 failed + retry_count < max_retry 的记录，且只重新投递 `generate_embedding` 任务
- 新任务增加两个能力：(1) 标记 stuck 状态的 pending/processing 记录为 failed (2) 记录汇总健康日志
- 两个任务职责不同：retry 任务负责重试，health 任务负责异常检测 + 日志汇报

**Alternatives considered**:
- 合并到现有 retry 任务：职责混淆，且 retry 任务频率（5 分钟）比健康检查需求（1 小时）高太多
- 替换现有 retry 任务：破坏已有功能，且 retry 的 5 分钟间隔对及时性有价值

## R6: 500ms 定时推送实现

**Decision**: 在 `AgentService.execute()` 的 `astream_events` 循环中，使用时间戳比较每 500ms 推送一次完整 MonitorData

**Rationale**:
- execute() 已是 async generator，自然支持异步定时逻辑
- 在 astream_events 迭代中检测时间差，超过 500ms 则推送当前 MonitorData 快照
- 流式响应期间数据自然更新（token 累加、工具调用变化），无需额外定时器
- 空闲时（非流式响应期间）不推送，仅在用户发消息时推送初始状态

**Alternatives considered**:
- asyncio.create_task 定时器：增加任务管理复杂度，需要与 execute 生命周期同步
- 前端 setInterval 轮询 REST API：转移了架构问题，增加服务器负载
- Celery 定时推送：粒度太粗，且需要跨进程传递运行时状态

## R7: MonitorData 数据组装

**Decision**: 扩展 context_status 事件 payload 为完整 MonitorData，在 execute() 中维护快照对象

**Rationale**:
- CPU 数据（input/output tokens）：从 LLM usage_metadata 中提取，已有 `_extract_usage()` 方法
- 内存数据（context breakdown）：从 `build_preamble_with_breakdown()` 返回的 TokenBreakdown 获取
- 硬盘数据（memory records）：从 `MemoryService.search_memory()` 结果中提取（已在 _build_prompt_preamble 中调用）
- 当前进程数据（tool processes）：在 astream_events 循环中检测 tool call 事件动态累加
- model_name：从 `model_service.get_active_model()` 获取

**Alternatives considered**:
- 拆分为多个事件类型（context_status + memory_status + tool_status）：前端合并逻辑复杂，状态同步困难
- 独立 REST API 获取记忆和工具数据：增加请求数，破坏"单一事件流"设计原则
- 独立数据聚合服务：过度抽象，数据源分散在 execute 流程各处

## R8: 折线图时间序列管理

**Decision**: 前端内存维护最近 60 个数据点，滑动窗口模式

**Rationale**:
- 500ms 推送频率下，60 个点覆盖约 30 秒趋势
- 足够观察一次 Agent 响应周期（通常 5-20 秒）
- 内存占用极小（每个数据点约 8 bytes × 3 系列 × 60 ≈ 1.4KB）
- 页面刷新或新对话时自然重置

**Alternatives considered**:
- 保留全部数据点：内存无限增长，长对话场景下影响性能
- 后端持久化历史：超出 scope，增加存储复杂度
- 15 个数据点：覆盖时间太短（7.5 秒），趋势不明显

## R9: 对话历史嵌入方式变更对 Token 计数的影响

**Decision**: TokenBreakdown 的 `history_messages` 字段计数来源于 `SystemMessage(name="conversation_history")` 的 content，而非独立的 HumanMessage/AIMessage 序列

**Rationale**:
- 对话历史已从独立 LangChain 消息序列重构为嵌入 SystemMessage 的文本块（通过 Jinja2 模板 `conversation_history.j2` 渲染）
- `_build_prompt_preamble()` 先构建不含历史的 preamble 计算 fixed_tokens，再将裁剪后的历史 dict 列表传给 `build_preamble(conversation_history=trimmed)`
- `agent.py` 的 `_wrap_prompt` 在 tool calling 循环中跳过 `name="conversation_history"` 的 SystemMessage，节省约 43-56% 重复 token
- MonitorData 推送的 breakdown 始终反映初始 preamble 的完整 token 分布，不随 tool loop 过滤而变化

**Impact on monitoring**:
- `build_preamble_with_breakdown()` 需要识别 `name="conversation_history"` 的 SystemMessage 并将其 content 的 token 数计入 `history_messages` 字段
- breakdown.total 包含 history_messages，即使 tool loop 中实际不发送该 SystemMessage
- 这是正确的设计：breakdown 描述的是上下文窗口的"设计组成"，而非每次 LLM 调用的实际输入
