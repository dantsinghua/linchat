# 特性规范：上下文与记忆管理 (M1b)

**特性分支**：`004-context-memory`
**创建日期**：2026-01-29
**状态**：草稿
**输入**：构建动态上下文窗口管理和数据库化的长期记忆系统，支持上下文裁剪/压缩、记忆 CRUD、向量检索、记忆总结
**范围**：本期仅后端 API（REST），不包含前端 UI，前端界面在后续里程碑中实现

## 前置依赖

- M1a 完成：`model` 表可用，能读取 `max_context_window` 字段
- PostgreSQL + pgvector 扩展
- OpenAI API 兼容的 Embedding 服务

## 澄清记录

### 2026-01-29

- 问：上下文窗口计算规则？ → 答：有效窗口 = max_context_window * 0.9，无硬性下限
- 问：裁剪时保留最近几轮对话？ → 答：默认保留最近 2 轮，可配置
- 问：记忆隔离如何保证？ → 答：所有查询必须带 `user_id` 过滤，必须有测试覆盖
- 问：Embedding 模型接口要求？ → 答：仅支持 OpenAI API 兼容接口，从 `model` 表获取配置
- 问：moltbot 参考实现是否在本项目中？ → 答：不在本项目中，仅作为设计参考

### 2026-01-30

- Q: Token 计数方式如何实现？ → A: 使用 tiktoken 库（cl100k_base 编码）精确计数
- Q: Embedding 向量维度是多少？ → A: 固定 2048，不支持动态修改。model 表中 embedding_dimensions 字段仅作参考记录，实际写入时硬编码 2048 并校验
- Q: 记忆管理是否需要用户界面？ → A: 本期仅后端 API，不做前端 UI，后续里程碑再加
- Q: 召回的记忆如何注入上下文？ → A: 作为独立的 system 消息插入到 system prompt 之后、对话历史之前
- Q: 同一用户并发触发压缩时如何处理？ → A: Redis 分布式锁，按 user_id 加锁，未获锁的请求等待锁释放后重新检查 token 是否仍超限
- Q: Embedding 服务不可用时的降级策略？ → A: 元数据正常写入，embedding 标记 `failed`，后台定时重试，不阻塞用户操作
- Q: 压缩 LLM 调用失败时的行为？ → A: 重试 3 次后回退到简单截断（丢弃最早消息），保证对话继续
- Q: Embedding 异步任务失败重试上限？ → A: 最多重试 3 次，超限后永久标记 `failed`，记忆仅支持关键词匹配
- Q: `user_memory` 表核心字段？ → A: `id`, `user_id`, `type`, `name`, `content`, `embedding_status`, `retry_count`, `tags`(jsonb预留), `importance_score`(float预留), `created_at`, `updated_at`；不含 `source_conversation_id`
- Q: 可观测性要求？ → A: Langfuse 追踪 LLM 调用 + Django logging 记录关键事件（embedding 失败、压缩触发、总结执行等）

---

## 用户场景与测试 *(必填)*

### 用户故事 1 — 动态上下文窗口管理（优先级：P0）

系统根据当前语言模型的 `max_context_window` 字段动态计算可用上下文窗口（取 90%），当对话历史超出窗口时自动裁剪最早的非 system 消息，保留最近 N 轮对话 + system prompt + 召回的记忆。

**优先级原因**：上下文管理是所有 LLM 对话的基础能力，直接影响对话质量和稳定性。

**独立测试**：可以通过构造超长对话历史，验证裁剪和压缩策略是否正确执行来独立测试。

**验收场景**：

1. **假设** 模型 `max_context_window` = 100,000，**当** 系统计算有效窗口，**则** 有效窗口 = 90,000 tokens
2. **假设** 完整 prompt（system prompt + 召回记忆 + 对话历史 + 用户当前输入）token 总数 ≥ 有效窗口，**当** 系统执行裁剪，**则** 从最早的非 system 消息开始丢弃，保留最近 2 轮对话 + system prompt + 召回记忆
4. **假设** 裁剪后仍超出窗口限制，**当** 系统触发 safeguard 压缩，**则** 调用 LLM 对被裁剪消息生成摘要，摘要替换原始消息，原始内容存入记忆表（type=`compaction`）

---

### 用户故事 2 — 长期记忆 CRUD（优先级：P0）

系统提供完整的记忆增删改查操作，所有记忆存储在 PostgreSQL `user_memory` 表中，并自动生成 embedding 存入 `user_memory_embedding` 表。用户之间的记忆严格隔离。

**优先级原因**：记忆 CRUD 是整个记忆系统的基础，后续的语义搜索、自动召回、记忆总结都依赖于此。

**独立测试**：可以通过 API 直接调用 CRUD 接口，验证数据正确性和用户隔离来独立测试。

**验收场景**：

1. **假设** 用户 A 创建一条记忆，**当** 系统处理创建请求，**则** `user_memory` 表写入记录，`embedding_status` = `pending`，异步任务生成 embedding 后状态变为 `done`
2. **假设** 用户 A 更新一条记忆，**当** 系统处理更新请求，**则** `user_memory` 表更新内容，旧 embedding 标记失效，异步重新生成 embedding
3. **假设** 用户 A 删除一条记忆，**当** 系统处理删除请求，**则** `user_memory` 表记录删除，`user_memory_embedding` 表级联删除
4. **假设** 用户 A 创建了记忆，**当** 用户 B 搜索记忆，**则** 搜索结果中不包含用户 A 的记忆（用户隔离）
5. **假设** 搜索时未提供 `user_id`，**当** 系统处理请求，**则** 返回错误，拒绝无用户标识的搜索

---

### 用户故事 3 — 语义搜索与自动召回（优先级：P1）

对话开始前，系统基于用户输入内容自动检索相关记忆（pgvector 向量检索），将召回的记忆注入上下文中，使 LLM 能利用历史知识进行回复。

**优先级原因**：语义搜索是记忆系统产生价值的关键路径，无此能力则记忆存储无意义。

**独立测试**：可以通过存入已知记忆，然后用语义相关的查询验证召回结果来独立测试。

**验收场景**：

1. **假设** 用户已有多条记忆，**当** 用户发送与某条记忆语义相关的消息，**则** 系统自动召回该记忆并注入上下文
2. **假设** 记忆的 `embedding_status` != `done`，**当** 执行语义搜索，**则** 该记忆退化为关键词匹配
3. **假设** 执行语义搜索，**当** 搜索完成，**则** 检索延迟 < 500ms

---

### 用户故事 4 — 记忆总结机制（优先级：P2）

系统支持三种记忆总结：对话压缩时主动触发的 `compaction` 总结、每日 00:00 的 `daily-summary`、每月 1 日的 `monthly-summary`。总结共用同一核心方法，数据来源支持降级策略。

**优先级原因**：记忆总结是长期记忆质量的保障，但不影响基本对话能力。

**独立测试**：可以通过模拟定时任务执行，验证总结生成和降级策略来独立测试。

**验收场景**：

1. **假设** 对话触发上下文压缩，**当** 压缩完成，**则** 自动生成 `compaction` 类型记忆
2. **假设** 每日定时任务触发，**当** 当天有 `compaction` 记忆，**则** 基于压缩记忆生成 `daily-summary`
3. **假设** 每日定时任务触发，**当** 当天无 `compaction` 记忆，**则** 降级到 `message` 表取原始对话生成总结
4. **假设** 每日定时任务触发，**当** 当天无任何对话或记忆，**则** 跳过，不生成空总结
5. **假设** 每月定时任务触发，**当** 当月有 `daily-summary`，**则** 基于每日摘要生成 `monthly-summary`

---

## 功能需求

> 交叉引用：[data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [behavior-model.md](behavior-model.md) | [process-model.md](process-model.md)

### FR-001：动态上下文窗口计算

- 从 `model` 表读取当前语言模型的 `max_context_window`
- 有效窗口 = `max_context_window * 0.9`
- Token 计数：使用 tiktoken 库，编码方式 `cl100k_base`
- 来源优先级：model 表 > 默认值

### FR-002：渐进式上下文裁剪

- 当完整 prompt（system prompt + 召回记忆 + 对话历史 + 用户当前输入）的 token 总数 ≥ 有效窗口时，优先丢弃最早的非 system 消息
- 始终保留：最近 N 轮对话（默认 2）+ system prompt + 召回的记忆
- 召回记忆注入方式：作为独立的 system 消息插入到 system prompt 之后、对话历史之前
- 裁剪不够时触发 safeguard 压缩

### FR-003：Safeguard 压缩

- 取出 prune_messages 返回的全部被裁剪消息，调用 LLM 生成摘要
- 用摘要替换原始消息
- 将压缩的原始内容存入记忆表（type=`compaction`）
- 重复直到完整 prompt token 总量 < 有效窗口（effective_window）
- 并发控制：Redis 分布式锁按 `user_id` 加锁，未获锁的请求等待锁释放后重新检查 token 是否仍超限
- LLM 调用失败处理：重试 3 次后回退到简单截断（丢弃最早消息），保证对话不中断

### FR-004：记忆 CRUD

- 完整的增删改查操作
- 用户通过 REST API 创建的记忆类型固定为 `memory`，API 不接受客户端指定 type 参数；`compaction`、`daily-summary`、`monthly-summary` 类型仅由系统内部流程创建
- `content` 最大长度 10,000 字符，超出由序列化器拒绝；Embedding 生成时若 token 数超出模型输入限制，截取前 N tokens 生成 embedding
- 创建/更新时 embedding 生命周期管理见 [FR-007](#fr-007两表数据同步)
- 删除时级联删除 embedding（FK ON DELETE CASCADE）
- `user_memory` 表字段定义（→ [data-model.md §2](data-model.md#2-表-1user_memory记忆元数据)）：
  - `id` (PK)
  - `user_id` (FK, 必填)
  - `type` (enum: `memory`/`compaction`/`daily-summary`/`monthly-summary`)
  - `name` (varchar, nullable, 如 `daily-2026-01-29`)
  - `content` (text, 记忆内容)
  - `embedding_status` (enum: `pending`/`processing`/`done`/`failed`, 默认 `pending`)
  - `retry_count` (int, 默认 0, embedding 重试计数)
  - `tags` (jsonb, nullable, 预留字段，暂不使用)
  - `importance_score` (float, nullable, 预留字段，暂不使用)
  - `created_at`, `updated_at` (timestamp)

### FR-005：用户记忆隔离

- 所有查询必须带 `user_id` 过滤
- 不带 `user_id` 的查询必须报错
- 必须有专门的隔离测试用例
- `user_id` 由视图层从 `request.user.user_id` 自动注入，API 不接受客户端传入。服务层所有方法的 `user_id` 参数均来自视图层透传

### FR-006：向量检索

- 基于 pgvector 的语义搜索
- Embedding 模型使用 OpenAI API 兼容接口
- 从 `model` 表获取 embedding 模型配置（API 地址、API Key）。向量维度固定 2048，不从 model 表读取
- `user_memory_embedding` 表的向量列固定维度 2048，写入时校验非 2048 报错
- 若 `model` 表中无 `type='embedding'` 的配置记录，embedding 生成任务抛出 `EmbeddingConfigNotFoundError` 异常并记录 WARNING 日志，记忆元数据正常写入，embedding_status 标记为 `failed`
- `embedding_status != 'done'` 的记录退化为关键词匹配

### FR-007：两表数据同步

- `user_memory` 为主表，`user_memory_embedding` 为从表
- 创建后异步生成 embedding，状态：`pending` → `processing` → `done` / `failed`
- 更新时旧 embedding 标记失效 → 新 embedding 写入 → 删除旧数据
- 定时扫描 `failed` / `pending` 超时记录，自动重试，最多重试 3 次
- 超过 3 次重试上限后永久标记 `failed`，该记忆退化为仅关键词匹配，不再自动重试
- Embedding 服务不可用时：元数据正常写入不阻塞，embedding 标记 `failed`，由后台定时任务重试

### FR-008：记忆总结

- 三种触发方式：主动压缩、每日定时、每月定时
- 共用核心总结方法 `summarize_and_store`
- 数据来源降级策略：压缩记忆 → message 表原始对话 → 跳过
- 活跃用户定义：每日总结 — 当天有新 `compaction` 记忆或新 `message` 记录的用户；每月总结 — 当月有 `daily-summary` 记忆或新 `message` 记录的用户

### FR-009：记忆类型

| type 值 | 来源 | 说明 |
|--------|------|------|
| `memory` | 用户/Agent 主动创建 | 通用记忆 |
| `compaction` | 对话压缩自动触发 | 单次压缩的会话摘要 |
| `daily-summary` | 每日定时任务 | 当天所有对话/压缩的汇总 |
| `monthly-summary` | 每月定时任务 | 当月所有每日摘要的汇总 |

---

## 非功能需求

### NFR-001：性能

- 语义搜索延迟 < 500ms（pgvector 语义搜索适用宪法第五条 ES 搜索同级标准）
- 上下文裁剪/压缩引入的额外延迟 < 500ms（不含 LLM 压缩摘要的等待时间，压缩摘要为同步阻塞操作，单独计量）

### NFR-002：数据一致性

- 两表数据最终一致，允许短暂延迟但不允许不一致
- 写操作原子性（事务保护）

### NFR-003：安全

- 用户记忆严格隔离，必须有测试覆盖
- 并发操作数据一致性（压缩操作通过 Redis 分布式锁按 `user_id` 粒度串行化）

### NFR-004：可观测性

- LLM 调用（压缩摘要、记忆总结）通过 Langfuse 追踪
- 关键事件使用 Django logging 记录：embedding 生成失败、压缩触发、定时总结执行、重试耗尽等
- 日志级别：失败/异常使用 WARNING 及以上，正常流程使用 INFO

---

*文档版本：v1.1*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — 补充 name 字段、功能需求交叉引用、analyze 修复（去16k下限/裁剪触发改完整prompt/维度固定2048/去conversation_id/user_id来源/活跃用户定义）*
