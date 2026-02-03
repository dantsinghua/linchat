# 规则模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [behavior-model.md](behavior-model.md) | [process-model.md](process-model.md)

---

## 业务规则

### R-001：有效上下文窗口计算

> 交叉引用：[spec.md FR-002](spec.md#fr-002动态上下文窗口计算) | [behavior-model.md §1](behavior-model.md#1-分层上下文组装)

- 有效窗口 = `model.max_context_window * 0.9`（预留 10% buffer）
- 来源优先级：model 表 > 默认值（128,000）
- Token 计数：tiktoken 库，编码方式 `cl100k_base`（→ R-017）
- 模型上下文窗口最小要求：≥ 10,000 tokens，不满足则拒绝使用并提示

### R-002：分层上下文组装

> 交叉引用：[spec.md FR-001](spec.md#fr-001分层上下文组装结构) | [spec.md FR-014](spec.md#fr-014动态-prompt-模板系统promptbuilder) | [behavior-model.md §1](behavior-model.md#1-分层上下文组装) | [data-model.md §7](data-model.md#7-上下文分层结构逻辑模型非持久化)

- 组装顺序：1(systemPrompt) → 2.a(模板) → 2.b(记忆) → 2.c(工具) → 2.d(前对话) → 2.e(用户输入)
- 约束：(1) + 2.a + 2.b + 2.c + 2.d + 2.e ≤ 有效上下文窗口
- 不可压缩部分（P0）：1(systemPrompt) + 2.a(模板) + 2.e(用户输入)，固定约 3k + 用户输入
- 前端输入框限制最多 4k tokens（2.e 部分）
- 全部加载完成后统一计算总 token 数是否超出有效窗口（与 spec.md FR-001/FR-003 一致）

### R-003：优先级驱动的上下文压缩

> 交叉引用：[spec.md FR-003](spec.md#fr-003优先级驱动的上下文压缩) | [behavior-model.md §2](behavior-model.md#2-优先级驱动的上下文压缩) | [process-model.md §2](process-model.md#2-优先级驱动的上下文压缩流程)

- 当各段内容总 token 数 ≥ 有效窗口时，按重要性从低到高依次压缩：
  1. 前对话(2.d) — 使用上下文工具（contextCompact/contextExtract/contextPrune）
  2. 工具内容(2.c) — 使用上下文工具
  3. 记忆内容(2.b) — 使用记忆工具（memSearch/memCache/memUpdate/memDelete）
- 用户当前输入(2.e)永远不压缩，永远保留原文
- 若 d → c → b 全部处理后仍超限，对处理结果统一再处理并直接截断超出内容
- 并发控制：Redis 分布式锁按 `user_id` 加锁，未获锁的请求等待锁释放后重新检查 token 是否仍超限
- 压缩过程中前端通过 SSE 事件显示状态（→ R-020）
- Redis 锁获取异常（Redis 服务不可用）时：降级为无锁执行压缩流程，记录 WARNING 日志，保证对话不中断。此降级可能导致同一用户并发请求重复压缩，但优先保障可用性

### R-004：用户记忆隔离（不可违背）

> 交叉引用：[spec.md FR-009](spec.md#fr-009用户记忆隔离) | [behavior-model.md §5](behavior-model.md#5-记忆-crud)

- 所有记忆查询必须携带 `user_id` 过滤条件
- 不携带 `user_id` 的查询必须抛出异常
- 用户 A 的记忆在任何场景下都不可被用户 B 访问
- `user_id` 由视图层从 `request.user.user_id` 自动注入，API 不接受客户端传入

### R-005：Embedding 状态流转

> 交叉引用：[data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- 合法状态：`pending` → `processing` → `done` | `failed`
- `failed` 状态可回退到 `processing`（重试），受 R-013 重试上限约束
- 只有 `done` 状态的记录参与向量检索
- 所有记录（不限 embedding_status）均参与关键词匹配（PostgreSQL 全文检索，tsvector + GIN + pg_jieba）
- 混合检索合并时，同一 memory_id 若同时出现在向量结果和关键词结果中，取加权和（vector_score × 0.7 + keyword_score × 0.3）；仅出现在单一通道的结果按该通道权重计分

### R-006：两表一致性

> 交叉引用：[spec.md FR-011](spec.md#fr-011两表数据同步) | [data-model.md §5](data-model.md#5-数据同步规则) | [process-model.md §5](process-model.md#5-两表数据同步时序)

- `user_memory` 为主表，`user_memory_embedding` 为从表
- 删除操作通过 FK CASCADE 保证一致性
- 创建/更新通过异步任务保证最终一致性
- 定时扫描 `failed` / `pending` 超时记录，自动重试（受 R-013 约束）

### R-007：记忆总结数据来源降级

> 交叉引用：[spec.md FR-012](spec.md#fr-012记忆总结) | [behavior-model.md §7](behavior-model.md#7-核心总结方法) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

- 每日总结：`compaction` 记忆 → `message` 表原始对话 → 跳过
- 每月总结：`daily-summary` 记忆 → `message` 表原始对话 → 跳过
- 无数据时不生成空总结
- 活跃用户定义：
  - 每日总结：当天有新 `compaction` 记忆或新 `message` 记录的用户
  - 每月总结：当月有 `daily-summary` 记忆或新 `message` 记录的用户

### R-008：记忆类型约束

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆-crud) | [spec.md FR-013](spec.md#fr-013记忆类型) | [data-model.md §2 type 字段](data-model.md#2-表-1user_memory记忆元数据)

- `type` 取值限定：`memory` / `compaction` / `daily-summary` / `monthly-summary`
- 用户通过 REST API 创建固定为 `memory`，API 不接受客户端指定 type 参数
- `compaction`、`daily-summary`、`monthly-summary` 仅由系统内部流程创建

### R-009：事务保护

> 交叉引用：[spec.md NFR-002](spec.md#nfr-002数据一致性) | [behavior-model.md §5](behavior-model.md#5-记忆-crud)

- 记忆创建/更新的元数据写入必须在事务中完成
- Embedding 生成为异步操作，不在同一事务中
- 删除操作利用数据库级联保证原子性

### R-010：搜索性能

> 交叉引用：[spec.md NFR-001](spec.md#nfr-001性能) | [spec.md FR-010](spec.md#fr-010向量检索)

- 语义搜索延迟 < 500ms
- 混合检索最多返回 5 条结果，按向量相似度（权重 0.7）与关键词匹配（权重 0.3）的加权得分排序
- 关键词匹配实现：PostgreSQL 全文检索（tsvector + GIN 索引），必须使用 pg_jieba 中文分词插件
- 上下文裁剪/压缩引入的额外延迟 < 500ms（不含 LLM 压缩摘要等待时间）

### R-011：Embedding 模型配置

> 交叉引用：[spec.md FR-010](spec.md#fr-010向量检索) | [data-model.md §3 embedding 字段](data-model.md#3-表-2user_memory_embedding记忆向量)

- 从 `model` 表获取 `type='embedding'` 的配置（API 地址、API Key）
- 仅支持 OpenAI API 兼容接口
- 向量维度固定 2048，不从 model 表读取。写入时校验非 2048 报错
- 若 `model` 表中无 `type='embedding'` 配置，抛出 `EmbeddingConfigNotFoundError`，embedding_status 标记为 `failed`

### R-012：定时任务执行

> 交叉引用：[spec.md FR-012](spec.md#fr-012记忆总结) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

- 每日总结：每天 00:00 执行
- 每月总结：每月 1 日 00:00 执行
- Embedding 重试扫描：可配置间隔（建议 5 分钟）

### R-013：Embedding 重试上限

> 交叉引用：[spec.md FR-011](spec.md#fr-011两表数据同步) | [data-model.md §2 retry_count](data-model.md#2-表-1user_memory记忆元数据) | [data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- Embedding 生成最多重试 3 次（`retry_count` 字段追踪）
- 超过 3 次后永久保持 `failed` 状态，不再自动重试
- 永久失败的记忆退化为仅关键词匹配（tsvector + GIN + pg_jieba）

### R-014：LLM 压缩失败回退

> 交叉引用：[spec.md FR-003](spec.md#fr-003优先级驱动的上下文压缩) | [behavior-model.md §2](behavior-model.md#2-优先级驱动的上下文压缩)

- 上下文压缩调用 LLM 失败时，重试 3 次
- 重试全部失败后回退到简单截断（丢弃最早消息）
- 回退截断不生成 `compaction` 记忆
- 保证对话流程不中断

### R-015：Embedding 服务不可用降级

> 交叉引用：[spec.md FR-011](spec.md#fr-011两表数据同步) | [behavior-model.md §5](behavior-model.md#5-记忆-crud) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- Embedding 服务不可用时，记忆元数据正常写入不阻塞用户操作
- `embedding_status` 标记为 `failed`
- 由后台定时任务重试（受 R-013 上限约束）

### R-016：可观测性

> 交叉引用：[spec.md NFR-004](spec.md#nfr-004可观测性)

- LLM 调用（压缩摘要、记忆总结、cronMem 事实抽取）通过 Langfuse 追踪
- 关键事件使用 Django logging 记录：embedding 生成失败、压缩触发、定时总结执行、重试耗尽
- 日志级别：失败/异常使用 WARNING 及以上，正常流程使用 INFO

### R-017：Token 计数方式

> 交叉引用：[spec.md FR-002](spec.md#fr-002动态上下文窗口计算)

- 使用 tiktoken 库，编码方式 `cl100k_base` 精确计数
- 所有 token 计算（窗口管理、压缩判断、各段 token 统计）统一使用此方式

### R-018：LangGraph 流程工具集隔离

> 交叉引用：[spec.md FR-007](spec.md#fr-007langgraph-流程编排) | [behavior-model.md §3](behavior-model.md#3-langgraph-流程编排) | [process-model.md §1](process-model.md#1-langgraph-四流程编排总览)

- chat 流程：记忆工具（本期）+ python repl + bravo search + home assistant（后续特性，本期仅预留注册接口）
- context 流程：仅上下文工具（contextCompact / contextExtract / contextPrune）
- memory 流程：仅记忆工具（memSearch / memCache / memUpdate / memDelete）
- cronMem 流程：无工具，仅 Agent → End
- 各流程工具集不可越界

### R-019：上下文超限安全兜底

> 交叉引用：[spec.md FR-015](spec.md#fr-015上下文超限安全兜底)

- 有效窗口 = max_context_window × 0.9，预留 10% 作为 buffer
- 若工具执行过程或中间过程导致上下文超过有效窗口但未超过最大窗口，利用 10% buffer 容纳
- 若超过模型最大上下文窗口（100%），直接截断内容
- 强制要求：确保任何使用场景下不会因超出模型上下文窗口而报错终止

### R-020：前端 SSE 压缩状态事件

> 交叉引用：[spec.md FR-016](spec.md#fr-016前端上下文压缩状态提示) | [process-model.md §6](process-model.md#6-前端-sse-压缩状态推送流程)

- 复用现有对话 SSE 流推送压缩状态事件，不开设独立通道
- 压缩开始：发送 `context_compacting` 事件
- 压缩完成：发送 `context_compacted` 事件
- 前端对话框左下角显示/隐藏"正在压缩上下文"状态标识

### R-021：content 最大长度

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆-crud) | [data-model.md §2](data-model.md#2-表-1user_memory记忆元数据)

- `user_memory.content` 最大 10,000 字符，超出由序列化器拒绝
- Embedding 生成时若 token 数超出模型输入限制，截取前 N tokens 生成 embedding

### R-022：cronMem LLM 调用失败

> 交叉引用：[spec.md FR-012](spec.md#fr-012记忆总结) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

- cronMem 流程 LLM 调用失败：重试 3 次后跳过该用户
- 记录 WARNING 日志
- 下次定时任务执行时重新尝试

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*更新日期：2026-01-31 — v2.1 R-010 明确混合检索加权比例（向量 0.7/关键词 0.3）及关键词匹配实现（tsvector + GIN + pg_jieba），R-005/R-013 补充降级实现细节*
