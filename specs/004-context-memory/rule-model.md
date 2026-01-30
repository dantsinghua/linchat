# 规则模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [behavior-model.md](behavior-model.md) | [process-model.md](process-model.md)

---

## 业务规则

### R-001：有效上下文窗口计算

> 交叉引用：[spec.md FR-001](spec.md#fr-001动态上下文窗口计算) | [behavior-model.md §1](behavior-model.md#1-上下文窗口计算)

- 有效窗口 = `model.max_context_window * 0.9`
- 来源优先级：model 表 > 默认值（128,000）

### R-002：裁剪保留规则

> 交叉引用：[spec.md FR-002](spec.md#fr-002渐进式上下文裁剪) | [behavior-model.md §2](behavior-model.md#2-渐进式上下文裁剪) | [process-model.md §2](process-model.md#2-上下文裁剪与压缩流程)

- 始终保留：system prompt + 最近 N 轮对话（默认 2）+ 召回的记忆
- **术语定义**："1 轮对话" = 1 条 role=user 消息 + 1 条 role=assistant 消息。保留最近 2 轮 = 保留最后 4 条 user/assistant 消息
- 丢弃顺序：从最早的非 system 消息开始

### R-003：压缩触发条件

> 交叉引用：[spec.md FR-003](spec.md#fr-003safeguard-压缩) | [behavior-model.md §3](behavior-model.md#3-safeguard-压缩) | [process-model.md §2](process-model.md#2-上下文裁剪与压缩流程)

- 仅在渐进式裁剪无法使完整 prompt token 总量降到有效窗口以下时触发
- 压缩内容必须同时存入 `user_memory`（type=`compaction`）
- 并发控制：Redis 分布式锁按 `user_id` 加锁，未获锁的请求等待锁释放后重新检查 token 是否仍超限

### R-004：用户记忆隔离（不可违背）

> 交叉引用：[spec.md FR-005](spec.md#fr-005用户记忆隔离) | [behavior-model.md §4](behavior-model.md#4-记忆-crud)

- 所有记忆查询必须携带 `user_id` 过滤条件
- 不携带 `user_id` 的查询必须抛出异常
- 用户 A 的记忆在任何场景下都不可被用户 B 访问

### R-005：Embedding 状态流转

> 交叉引用：[data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- 合法状态：`pending` → `processing` → `done` | `failed`
- `failed` 状态可回退到 `pending`（重试），受 R-013 重试上限约束
- 只有 `done` 状态的记录参与向量检索
- `pending` / `processing` / `failed` 状态的记录退化为关键词匹配

### R-006：两表一致性

> 交叉引用：[spec.md FR-007](spec.md#fr-007两表数据同步) | [data-model.md §5](data-model.md#5-数据同步规则) | [process-model.md §5](process-model.md#5-两表数据同步时序)

- `user_memory` 为主表，`user_memory_embedding` 为从表
- 删除操作通过 FK CASCADE 保证一致性
- 创建/更新通过异步任务保证最终一致性
- 定时扫描 `failed` / `pending` 超时记录，自动重试（受 R-013 约束）

### R-007：记忆总结数据来源降级

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆总结) | [behavior-model.md §6](behavior-model.md#6-核心总结方法) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

- 每日总结：`compaction` 记忆 → `message` 表原始对话 → 跳过
- 每月总结：`daily-summary` 记忆 → `message` 表原始对话 → 跳过
- 无数据时不生成空总结

### R-008：记忆类型约束

> 交叉引用：[spec.md FR-009](spec.md#fr-009记忆类型) | [data-model.md §2 type 字段](data-model.md#2-表-1user_memory记忆元数据)

- `type` 取值限定：`memory` / `compaction` / `daily-summary` / `monthly-summary`
- 后续扩展预留：`image` / `file` / `audio` / `video`（M1b 不实现）

### R-009：事务保护

> 交叉引用：[spec.md NFR-002](spec.md#nfr-002数据一致性) | [behavior-model.md §4](behavior-model.md#4-记忆-crud)

- 记忆创建/更新的元数据写入必须在事务中完成
- Embedding 生成为异步操作，不在同一事务中
- 删除操作利用数据库级联保证原子性

### R-010：搜索性能

> 交叉引用：[spec.md NFR-001](spec.md#nfr-001性能) | [behavior-model.md §4 search_memory](behavior-model.md#4-记忆-crud)

- 语义搜索延迟 < 500ms
- 默认返回 top 5 相关记忆

### R-011：Embedding 模型配置

> 交叉引用：[spec.md FR-006](spec.md#fr-006向量检索) | [data-model.md §3 embedding 字段](data-model.md#3-表-2user_memory_embedding记忆向量)

- 从 `model` 表获取 `type='embedding'` 的配置（API 地址、API Key）
- 仅支持 OpenAI API 兼容接口
- 向量维度固定 2048。若 model 表中 `embedding_dimensions` != 2048，写入时报错拒绝

### R-012：定时任务执行

> 交叉引用：[process-model.md §4](process-model.md#4-记忆总结定时任务流程)

- 每日总结：每天 00:00 执行
- 每月总结：每月 1 日 00:00 执行
- Embedding 重试扫描：可配置间隔（建议 5 分钟）

### R-013：Embedding 重试上限

> 交叉引用：[spec.md FR-007](spec.md#fr-007两表数据同步) | [data-model.md §2 retry_count](data-model.md#2-表-1user_memory记忆元数据) | [data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- Embedding 生成最多重试 3 次（`retry_count` 字段追踪）
- 超过 3 次后永久保持 `failed` 状态，不再自动重试
- 永久失败的记忆退化为仅关键词匹配

### R-014：LLM 压缩失败回退

> 交叉引用：[spec.md FR-003](spec.md#fr-003safeguard-压缩) | [behavior-model.md §3](behavior-model.md#3-safeguard-压缩) | [process-model.md §2](process-model.md#2-上下文裁剪与压缩流程)

- Safeguard 压缩调用 LLM 失败时，重试 3 次
- 重试全部失败后回退到简单截断（丢弃最早消息）
- 回退截断不生成 `compaction` 记忆
- 保证对话流程不中断

### R-015：Embedding 服务不可用降级

> 交叉引用：[spec.md FR-007](spec.md#fr-007两表数据同步) | [behavior-model.md §4](behavior-model.md#4-记忆-crud) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

- Embedding 服务不可用时，记忆元数据正常写入不阻塞用户操作
- `embedding_status` 标记为 `failed`
- 由后台定时任务重试（受 R-013 上限约束）

### R-016：可观测性

> 交叉引用：[spec.md NFR-004](spec.md#nfr-004可观测性)

- LLM 调用（压缩摘要、记忆总结）通过 Langfuse 追踪
- 关键事件使用 Django logging 记录：embedding 生成失败、压缩触发、定时总结执行、重试耗尽
- 日志级别：失败/异常使用 WARNING 及以上，正常流程使用 INFO

### R-017：Token 计数方式

> 交叉引用：[spec.md FR-001](spec.md#fr-001动态上下文窗口计算) | [behavior-model.md §1/§2](behavior-model.md#1-上下文窗口计算)

- 使用 tiktoken 库，编码方式 `cl100k_base` 精确计数
- 所有 token 计算（窗口管理、裁剪判断）统一使用此方式

---

*文档版本：v1.1*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — 补充 R-003 并发控制、R-013~R-017 新规则、全文交叉引用、analyze 修复（去16k下限/维度固定2048/conversation_id→user_id）*
