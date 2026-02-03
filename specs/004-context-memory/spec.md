# 特性规范：上下文与记忆管理 (M1b)

**特性分支**：`004-context-memory`
**创建日期**：2026-01-29
**状态**：草稿
**输入**：构建动态上下文窗口管理和数据库化的长期记忆系统，支持分层上下文组装、优先级驱动的压缩策略、记忆 CRUD、向量检索、记忆总结、LangGraph 多流程编排
**范围**：后端 API（REST）+ 前端上下文压缩状态提示，不包含完整的记忆管理前端 UI

## 前置依赖

- M1a 完成：`model` 表可用，能读取 `max_context_window` 字段
- PostgreSQL + pgvector 扩展 + pg_jieba 中文分词插件
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
- Q: 上下文分层组装结构？ → A: 采用 systemPrompt(固定~2k) + userPrompt 五段式结构(2.a模板~1k + 2.b记忆 + 2.c工具 + 2.d前对话 + 2.e用户输入)，按优先级依次加载并自动压缩
- Q: 压缩处理顺序？ → A: 按重要性从低到高：先压缩前对话(d)，再压缩工具内容(c)，最后处理记忆(b)，b的处理需调用记忆工具
- Q: 用户输入是否参与压缩？ → A: 永远不压缩用户当前输入(2.e)，永远保留原文
- Q: 前端限制？ → A: 输入框最多 4k tokens，固定 token 数为 2k+1k=3k，因此模型上下文窗口需 ≥ 10,000 tokens
- Q: LangGraph 需要几个流程？ → A: 四个——chat(主对话)、context(上下文处理)、memory(记忆处理)、cronMem(定时记忆总结)
- Q: 上下文超限的安全兜底？ → A: 10% buffer 预留，超过模型最大上下文则直接截断，确保不会因超限报错
- Q: 记忆搜索结果的 TopK 和排序策略？ → A: 混合检索最多返回 5 条结果，按向量相似度与关键词匹配的加权得分排序
- Q: context/memory 流程与 chat 流程的调用关系？ → A: 串行前置——先执行 context/memory 流程完成压缩，再进入 chat 流程
- Q: cronMem 流程 LLM 调用失败的处理策略？ → A: 重试 3 次后跳过该用户，记录日志，下次定时任务时重新尝试
- Q: 压缩状态 SSE 事件类型？ → A: 复用现有对话 SSE 流，事件类型 `context_compacting`（开始）/ `context_compacted`（完成）

### 2026-01-31

- Q: 混合检索的加权比例？ → A: 向量相似度 0.7 / 关键词匹配 0.3（语义优先）
- Q: 关键词匹配的实现方式？ → A: PostgreSQL 全文检索（tsvector + GIN 索引），必须安装 pg_jieba 中文分词插件

---

## 用户场景与测试 *(必填)*

### 用户故事 1 — 分层上下文组装与动态窗口管理（优先级：P0）

系统采用分层结构组装发送给大模型的完整上下文。上下文由以下五段按顺序组装：

1. **systemPrompt**（固定，约 2k tokens）：基础角色 + 行为规范
2. **userPrompt** 五段式结构：
   - **2.a 模板内容**（固定，约 1k tokens）：prompt 模板的固定部分
   - **2.b 记忆内容**（动态）：从记忆系统召回的相关记忆
   - **2.c 工具内容**（动态）：工具定义和工具调用结果
   - **2.d 前对话**（动态）：历史对话记录
   - **2.e 用户当前输入**（动态）：用户最新发送的消息

系统按 1 → 2.a → 2.b → 2.c → 2.d → 2.e 的顺序全部加载各段内容，加载完成后计算总 token 数。若总 token 数超出有效上下文窗口（= max_context_window × 0.9），则按 d → c → b 的顺序依次压缩（重要性从低到高），直到总 token 数降至有效窗口以内。

**约束条件**：
- 有效上下文 ≥ (1)tokens + 2.a tokens + 2.b tokens + 2.c tokens + 2.d tokens + 2.e tokens
- 固定部分（1 + 2.a + 2.e）的 token 数量不可变
- 前端输入框限制最多 4k tokens（2.e 部分），固定 token 数为 2k+1k=3k，因此模型上下文窗口需确保 ≥ 10,000 tokens（即 7k / 0.9 ≈ 8k，取安全余量 10k）

**优先级原因**：上下文组装是所有 LLM 对话的基础能力，直接影响对话质量和稳定性。

**独立测试**：可以通过构造不同大小的各段内容，验证组装顺序、token 计算和压缩触发是否正确。

**验收场景**：

1. **假设** 模型 `max_context_window` = 100,000，**当** 系统计算有效窗口，**则** 有效窗口 = 90,000 tokens
2. **假设** 各段内容总 token 数未超出有效窗口，**当** 系统组装上下文，**则** 所有段按原文加载，不触发压缩
3. **假设** 全部加载后总 token 数超出有效窗口，**当** 系统触发压缩，**则** 先压缩前对话(2.d)，若仍超限再压缩工具内容(2.c)，最后处理记忆内容(2.b)
4. **假设** 全部加载后超限且用户输入(2.e)较大，**当** 系统处理，**则** 不压缩用户输入，仅对 d → c → b 进行压缩处理
5. **假设** 模型上下文窗口 < 10,000 tokens，**当** 系统校验模型配置，**则** 拒绝使用该模型并提示上下文窗口不足

---

### 用户故事 2 — 优先级驱动的上下文压缩（优先级：P0）

当上下文总 token 数超出有效窗口时，系统按重要性从低到高的顺序依次压缩各段内容：

1. **第一步**：压缩前对话(2.d) — 使用上下文工具（contextCompact/contextExtract/contextPrune）
2. **第二步**：若仍超限，压缩工具内容(2.c) — 使用上下文工具
3. **第三步**：若仍超限，处理记忆内容(2.b) — 使用记忆工具（memSearch/memCache/memUpdate/memDelete）
4. **第四步**：若所有段处理完仍超限，对 d + c + b 处理后的内容再次统一处理并直接截断超出部分

压缩过程中，前端对话框左下角显示"正在压缩上下文"状态提示，压缩完成后提示消失。

**优先级原因**：压缩策略直接影响对话连续性，错误的压缩顺序会导致关键信息丢失。

**独立测试**：可以通过构造超长上下文，验证压缩顺序和每步结果来独立测试。

**验收场景**：

1. **假设** 前对话(2.d)超大而记忆和工具内容较小，**当** 系统触发压缩，**则** 仅压缩前对话即可满足窗口限制，不处理 2.c 和 2.b
2. **假设** 压缩前对话(2.d)后仍超限，**当** 系统继续处理，**则** 接着压缩工具内容(2.c)
3. **假设** 压缩 2.d 和 2.c 后仍超限，**当** 系统继续处理，**则** 使用记忆工具处理记忆内容(2.b)
4. **假设** 所有段压缩后仍超限，**当** 系统执行最终处理，**则** 对处理后的内容直接截断至有效窗口大小
5. **假设** 触发上下文压缩，**当** 前端接收到压缩状态事件，**则** 对话框左下角显示"正在压缩上下文"提示
6. **假设** 上下文压缩完成，**当** 前端接收到完成事件，**则** 压缩状态提示消失

---

### 用户故事 3 — 长期记忆 CRUD（优先级：P0）

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

### 用户故事 4 — 语义搜索与自动召回（优先级：P1）

对话开始前，系统基于用户输入内容自动检索相关记忆（pgvector 向量检索 + 关键词混合检索），将召回的记忆注入上下文中，使 LLM 能利用历史知识进行回复。

**优先级原因**：语义搜索是记忆系统产生价值的关键路径，无此能力则记忆存储无意义。

**独立测试**：可以通过存入已知记忆，然后用语义相关的查询验证召回结果来独立测试。

**验收场景**：

1. **假设** 用户已有多条记忆，**当** 用户发送与某条记忆语义相关的消息，**则** 系统自动召回该记忆并注入上下文
2. **假设** 记忆的 `embedding_status` != `done`，**当** 执行语义搜索，**则** 该记忆退化为关键词匹配
3. **假设** 执行语义搜索，**当** 搜索完成，**则** 检索延迟 < 500ms

---

### 用户故事 5 — 记忆总结机制（优先级：P2）

系统支持三种记忆总结：对话压缩时主动触发的 `compaction` 总结、每日 00:00 的 `daily-summary`、每月 1 日的 `monthly-summary`。总结共用同一核心方法，数据来源支持降级策略。

定时记忆总结（cronMem 流程）使用专用的 system prompt（参考 mem0 的 prompt 设计），仅做事实抽取和记忆打标工作，输出 content、tags、更新 date 等。

**优先级原因**：记忆总结是长期记忆质量的保障，但不影响基本对话能力。

**独立测试**：可以通过模拟定时任务执行，验证总结生成和降级策略来独立测试。

**验收场景**：

1. **假设** 对话触发上下文压缩，**当** 压缩完成，**则** 自动生成 `compaction` 类型记忆
2. **假设** 每日定时任务触发，**当** 当天有 `compaction` 记忆，**则** 基于压缩记忆生成 `daily-summary`
3. **假设** 每日定时任务触发，**当** 当天无 `compaction` 记忆，**则** 降级到 `message` 表取原始对话生成总结
4. **假设** 每日定时任务触发，**当** 当天无任何对话或记忆，**则** 跳过，不生成空总结
5. **假设** 每月定时任务触发，**当** 当月有 `daily-summary`，**则** 基于每日摘要生成 `monthly-summary`

---

### 用户故事 6 — LangGraph 多流程编排（优先级：P0）

系统通过 LangGraph 编排四个独立的 Agent 流程，每个流程有明确的职责边界和工具集：

1. **chat 流程**（主对话）：整个上下文 → Agent → Tool → End
   - 工具集：所有记忆工具 + python repl + bravo search + home assistant
2. **context 流程**（上下文处理）：(1)tokens + 2.a tokens + 2.e tokens + 对应内容(2.c、2.d) → Agent → Tool → End
   - 工具集：仅上下文工具（contextCompact / contextExtract / contextPrune）
   - 内容超长时直接截断处理
3. **memory 流程**（记忆处理）：(1)tokens + 2.a tokens + 2.e tokens + 2.b → Agent → Tool → End
   - 工具集：仅记忆工具（memSearch / memCache / memUpdate / memDelete）
   - 内容超长时直接截断处理
4. **cronMem 流程**（定时记忆总结）：专用 system prompt（参考 mem0 prompt 设计）+ 对应记忆内容 → Agent → End
   - 不注册工具，仅做事实抽取、记忆打标
   - 输出：content、tags、更新 date 等

**优先级原因**：LangGraph 流程编排是整个系统的骨架，决定了各功能模块如何协作。

**独立测试**：可以通过独立启动每个流程，验证工具集限制和流程正确性来独立测试。

**验收场景**：

1. **假设** 用户发送普通消息，**当** 上下文未超限，**则** 直接进入 chat 流程处理
2. **假设** 用户发送消息后上下文超限，**当** 系统检测到需要压缩，**则** 先执行 context 流程处理 2.d 和 2.c，必要时执行 memory 流程处理 2.b，完成后进入 chat 流程
3. **假设** context 流程中传入的内容超长，**当** 系统组装 context 流程的上下文，**则** 直接截断超出部分
4. **假设** cronMem 定时任务触发，**当** 系统执行记忆总结，**则** 使用专用 prompt 进行事实抽取和打标

---

### 用户故事 7 — 前端上下文压缩状态提示（优先级：P1）

当系统触发上下文压缩过程时，前端对话框左下角实时显示压缩状态，告知用户系统正在处理中。

**优先级原因**：用户体验的重要组成部分，避免用户在等待时感到困惑。

**独立测试**：可以通过触发压缩操作，验证前端状态变化来独立测试。

**验收场景**：

1. **假设** 系统开始上下文压缩，**当** 前端收到压缩开始事件，**则** 对话框左下角显示"正在压缩上下文"提示
2. **假设** 系统完成上下文压缩，**当** 前端收到压缩完成事件，**则** 提示消失
3. **假设** 压缩过程中用户切换会话，**当** 用户返回该会话，**则** 若压缩仍在进行则继续显示提示

---

### 边缘案例

- **上下文超限安全兜底**：若因工具执行过程或中间原因导致上下文超过有效窗口（90%），还有预留的 10% buffer；若超过模型最大上下文，则直接截断内容，确保不会因超限报错终止
- **并发压缩**：同一用户并发触发压缩时，Redis 分布式锁按 user_id 加锁，未获锁的请求等待后重新检查
- **Embedding 服务不可用**：元数据正常写入，embedding 标记 `failed`，后台定时重试，不阻塞用户操作
- **压缩 LLM 调用失败**：重试 3 次后回退到简单截断（丢弃最早消息），保证对话继续
- **所有段压缩后仍超限**：对 d + c + b 的处理结果再次统一处理并直接截断超出内容
- **模型上下文窗口过小**：若模型 max_context_window < 10,000 tokens，拒绝使用并提示
- **空记忆场景**：无记忆内容时 2.b 段为空（0 tokens），不影响其他段的加载和计算
- **cronMem LLM 调用失败**：重试 3 次后跳过该用户，记录日志，下次定时任务时重新尝试

---

## 功能需求

> 交叉引用：[data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [behavior-model.md](behavior-model.md) | [process-model.md](process-model.md)

### FR-001：分层上下文组装结构

系统按以下分层结构组装发送给大模型的完整上下文：

| 层级 | 内容 | 固定/动态 | 预估 Token 数 |
|------|------|-----------|---------------|
| 1 | systemPrompt：基础角色 + 行为规范 | 固定 | ~2k |
| 2.a | prompt 模板固定部分 | 固定 | ~1k |
| 2.b | 记忆内容（从记忆系统召回） | 动态 | 0 ~ 不固定 |
| 2.c | 工具内容（工具定义 + 调用结果） | 动态 | 0 ~ 不固定 |
| 2.d | 前对话（历史对话记录） | 动态 | 0 ~ 不固定 |
| 2.e | 用户当前输入 | 动态 | 0 ~ 4k（前端限制） |

- 组装顺序：1 → 2.a → 2.b → 2.c → 2.d → 2.e
- 约束：(1) + 2.a + 2.b + 2.c + 2.d + 2.e ≤ 有效上下文窗口
- 用户当前输入(2.e)永远不压缩，永远保留原文

### FR-002：动态上下文窗口计算

- 从 `model` 表读取当前语言模型的 `max_context_window`
- 有效窗口 = `max_context_window * 0.9`（预留 10% buffer）
- Token 计数：使用 tiktoken 库，编码方式 `cl100k_base`
- 来源优先级：model 表 > 默认值
- 模型上下文窗口最小要求：≥ 10,000 tokens

### FR-003：优先级驱动的上下文压缩

当各段内容总 token 数 ≥ 有效窗口时，按以下顺序压缩（重要性从低到高）：

1. **前对话(2.d)** — 使用上下文工具处理
2. **工具内容(2.c)** — 使用上下文工具处理
3. **记忆内容(2.b)** — 使用记忆工具处理

压缩规则：
- 全部加载后检查：总 token 数 ≥ 有效窗口时，按 d → c → b 顺序依次压缩
- 用户当前输入(2.e)永远不压缩
- 若 d → c → b 全部处理后仍超限，对处理结果统一再处理并直接截断超出内容
- 并发控制：Redis 分布式锁按 `user_id` 加锁
- 工具选择策略：每个流程中 Agent（LLM）根据 system prompt 中的工具使用规范自主决定调用哪些注册工具，系统仅通过流程级工具集隔离限制可用工具范围，不做预定义调用序列

### FR-004：上下文工具集（仅上下文处理流程使用）

| 工具名称 | 功能 | 说明 |
|----------|------|------|
| contextCompact | 压缩总结 | 将长对话压缩成摘要 |
| contextExtract | 片段抽取 | 从之前对话内容中检索抽取相关片段 |
| contextPrune | 删除剪枝 | 将对话多余的内容去掉 |

### FR-005：记忆工具集（记忆处理流程 + 主流程均可使用）

| 工具名称 | 功能 | 说明 |
|----------|------|------|
| memSearch | 记忆查询 | 混合检索（关键字 + 向量检索）PostgreSQL 中的记忆表数据，返回结果 |
| memCache | 记忆存储 | 将用户强调的内容写入 PostgreSQL 表中（之后定时同步到向量库） |
| memUpdate | 记忆更新 | 将一至多个记忆进行数据更新操作 |
| memDelete | 记忆删除 | 将一至多个记忆进行删除操作 |

### FR-006：对话工具集（主对话流程使用）

| 工具名称 | 功能 |
|----------|------|
| python repl | Python 代码执行 |
| bravo search | 网络搜索 |
| home assistant | 智能家居控制 |

> **注**：python repl / bravo search / home assistant 三个对话工具将在后续独立特性中实现。本期（004-context-memory）仅预留工具注册接口，chat 流程暂时仅包含记忆工具集。
>
> **预留接口设计**：chat 流程工厂函数接受 `extra_tools: list[BaseTool]` 参数（默认空列表），当前仅传入记忆工具集。后续特性（python repl / bravo search / home assistant）通过此参数注入对话工具，无需修改工厂函数签名。

### FR-007：LangGraph 流程编排

系统定义四个独立的 LangGraph 流程：

| 流程 | 输入上下文 | 工具集 | 说明 |
|------|-----------|--------|------|
| chat | 完整上下文（1 + 2.a~2.e） | 记忆工具 + ~~python repl + bravo search + home assistant~~（本期仅记忆工具，其他后续特性预留） | 主对话流程 |
| context | (1) + 2.a + 2.e + 对应内容(2.c/2.d) | 仅上下文工具 | 上下文处理，超长直接截断 |
| memory | (1) + 2.a + 2.e + 2.b | 仅记忆工具 | 记忆处理，超长直接截断 |
| cronMem | 专用 system prompt + 记忆内容 | 无工具（仅 Agent → End） | 定时记忆总结，事实抽取与打标 |

- context 和 memory 流程中，若传入内容超长则直接截断处理。截断策略：各子流程可用 token 预算 = 有效窗口 - 固定部分 token 数（层级 1 + 2.a + 2.e）；从内容**尾部**截断至预算以内；截断时保持完整消息边界（不截断半条 user/assistant 消息）
- 流程调用关系：串行前置——上下文超限时先执行 context 流程（处理 2.d 和 2.c），必要时再执行 memory 流程（处理 2.b），全部完成后才进入 chat 流程
- cronMem 使用参考 mem0 prompt 设计的专用 system prompt，仅做事实抽取、记忆打标，输出 content、tags、更新 date 等

### FR-008：记忆 CRUD

- 完整的增删改查操作
- 用户通过 REST API 创建的记忆类型固定为 `memory`，API 不接受客户端指定 type 参数；`compaction`、`daily-summary`、`monthly-summary` 类型仅由系统内部流程创建
- `content` 最大长度 10,000 字符，超出由序列化器拒绝；Embedding 生成时若 token 数超出模型输入限制，截取前 N tokens 生成 embedding
- 创建/更新时 embedding 生命周期管理见 [FR-011](#fr-011两表数据同步)
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

### FR-009：用户记忆隔离

- 所有查询必须带 `user_id` 过滤
- 不带 `user_id` 的查询必须报错
- 必须有专门的隔离测试用例
- `user_id` 由视图层从 `request.user.user_id` 自动注入，API 不接受客户端传入。服务层所有方法的 `user_id` 参数均来自视图层透传

### FR-010：向量检索

- 基于 pgvector 的语义搜索 + 关键词混合检索
- 混合检索最多返回 5 条结果，按向量相似度（权重 0.7）与关键词匹配（权重 0.3）的加权得分排序
- Embedding 模型使用 OpenAI API 兼容接口
- 从 `model` 表获取 embedding 模型配置（API 地址、API Key）。向量维度固定 2048，不从 model 表读取
- `user_memory_embedding` 表的向量列固定维度 2048，写入时校验非 2048 报错
- 若 `model` 表中无 `type='embedding'` 的配置记录，embedding 生成任务抛出 `EmbeddingConfigNotFoundError` 异常并记录 WARNING 日志，记忆元数据正常写入，embedding_status 标记为 `failed`
- `embedding_status != 'done'` 的记录退化为关键词匹配（PostgreSQL 全文检索，tsvector + GIN 索引，必须使用 pg_jieba 中文分词插件）

### FR-011：两表数据同步

- `user_memory` 为主表，`user_memory_embedding` 为从表
- 创建后异步生成 embedding，状态：`pending` → `processing` → `done` / `failed`
- 更新时旧 embedding 标记失效 → 新 embedding 写入 → 删除旧数据
- 定时扫描 `failed` / `pending` 超时记录，自动重试，最多重试 3 次
- 超过 3 次重试上限后永久标记 `failed`，该记忆退化为仅关键词匹配，不再自动重试
- Embedding 服务不可用时：元数据正常写入不阻塞，embedding 标记 `failed`，由后台定时任务重试

### FR-012：记忆总结

- 三种触发方式：主动压缩、每日定时、每月定时
- 共用核心总结方法 `summarize_and_store`
- 数据来源降级策略：压缩记忆 → message 表原始对话 → 跳过
- 活跃用户定义：每日总结 — 当天有新 `compaction` 记忆或新 `message` 记录的用户；每月总结 — 当月有 `daily-summary` 记忆或新 `message` 记录的用户
- cronMem 流程使用参考 mem0 prompt 设计的专用 system prompt，仅做事实抽取和记忆打标
- cronMem LLM 调用失败处理：重试 3 次后跳过该用户，记录 WARNING 日志，下次定时任务执行时重新尝试

### FR-013：记忆类型

> 记忆类型枚举定义见 [FR-008](#fr-008记忆-crud) `type` 字段。本节仅补充各类型的创建来源和用途。

| type 值 | 创建来源 | 用途 |
|--------|----------|------|
| `memory` | 用户 REST API / Agent memCache 工具 | 通用记忆 |
| `compaction` | ContextService 压缩完成后自动创建 | 单次压缩的会话摘要 |
| `daily-summary` | Celery 每日定时任务 | 当天所有对话/压缩的汇总 |
| `monthly-summary` | Celery 每月定时任务 | 当月所有每日摘要的汇总 |

### FR-014：动态 Prompt 模板系统（PromptBuilder）

系统提供统一的 prompt 组装引擎（`apps/chat/prompts.py`），支持按功能模块动态加载 system prompt、注入对话历史、召回记忆、工具上下文和用户输入。

#### 分层组装结构

参考 Claude Code 的多层 system 消息模式，最终消息列表的结构为：

```
[system]  基础角色 + 行为/推理/安全规范 + 功能模块        ← 层级 1（~2k tokens）
[system]  prompt 模板固定部分                             ← 层级 2.a（~1k tokens）
[system]  召回记忆（可选）                                ← 层级 2.b（动态）
[system]  工具定义（可选）                                ← 层级 2.c（动态）
[user/assistant]  最近 N 轮对话历史                       ← 层级 2.d（动态）
[user]    当前用户输入                                    ← 层级 2.e（动态，不可压缩）
```

#### PromptBuilder 依赖注入

PromptBuilder 通过构造函数注入外部依赖：

| 依赖 | 类型 | 用途 |
|------|------|------|
| `config` | `PromptConfig` | 用户 ID、模型配置、keep_recent_rounds 等 |
| `chat_repository` | `ChatRepository` | 查询 message 表获取历史对话（层级 2.d） |
| `memory_service` | `MemoryService` | 调用 retrieve_relevant_memories 获取记忆（层级 2.b） |

> PromptBuilder 不直接操作 ORM，所有数据访问通过注入的仓库/服务层完成（宪法 1.1 关注点分离）。

#### PromptBuilder 核心方法

| 方法 | 职责 |
|------|------|
| `build_system_prompt()` | 基础角色 + 行为规范 + 功能模块组装（层级 1） |
| `build_template_block()` | prompt 模板固定部分（层级 2.a）：**输出格式规范**（JSON/Markdown 偏好）、**回复长度引导**（简洁/详细模式切换）、**对话上下文窗口声明**（告知 LLM 当前上下文容量和已用量）。模板结构由 PromptBuilder 硬编码，不依赖外部配置；其中上下文窗口声明的数值（有效窗口大小、当前已用 token 数）在运行时通过模板占位符动态填充 |
| `build_memory_block()` | 召回记忆注入为独立 system 消息（层级 2.b） |
| `build_tool_context()` | 工具定义注入（层级 2.c） |
| `build_conversation_history()` | 短期对话历史（层级 2.d），从 `message` 表按 `user_id` 查询最近 N×2 条 role=user/assistant 消息（N = `PromptConfig.keep_recent_rounds`，硬编码默认值 2，即 4 条消息），按 `created_at` 升序排列。数据通过构造函数注入的 `ChatRepository.get_recent_messages(user_id, limit)` 获取，本期不支持 API 传入或动态配置 |
| `build_messages()` | 最终消息列表（dict 格式） |
| `build_messages_for_langchain()` | LangChain 格式消息列表 |

#### 功能模块注册机制（PromptModule 枚举 + PromptRegistry）

| 模块 | 说明 | 加载条件 |
|------|------|----------|
| `BASE` | 语言风格、准确性、安全隐私 | 始终加载 |
| `REASONING` | 结构化思考、任务分解、上下文感知 | 按需启用 |
| `TOOL_USAGE` | 工具调用原则和规范 | 有可用工具时启用 |
| `CODE_ASSIST` | 代码辅助专项 | 按需启用 |
| `CREATIVE_WRITING` | 创意写作专项 | 按需启用 |
| `DATA_ANALYSIS` | 数据分析专项 | 按需启用 |

- 支持 `register_custom_module()` 运行时动态扩展自定义模块

#### Token 裁剪优先级

使用 Level 编号表示裁剪顺序（**L 越小越先被压缩**，与 FR-003 "重要性从低到高 d→c→b" 一致）：

| 裁剪级别 | 内容 | 层级 | 说明 |
|----------|------|------|------|
| L0-PROTECTED（不可丢弃） | 基础 system prompt + prompt 模板 + 当前用户输入 | 1 + 2.a + 2.e | 固定部分，不可压缩 |
| L1-FIRST（最先压缩） | 前对话历史 | 2.d | 第一步处理 |
| L2-SECOND（其次压缩） | 工具内容 | 2.c | 第二步处理 |
| L3-LAST（最后压缩） | 记忆内容 | 2.b | 最后才处理 |

#### 固定话术覆盖范围

语言匹配、Markdown 格式化、诚实性约束、安全隐私保护、prompt 泄露防御、工具使用规范、记忆参考引导

#### 专用 Prompt 模板

| 模板 | 用途 | 调用方 |
|------|------|--------|
| `COMPACTION_PROMPT_TEMPLATE` | 对话压缩摘要生成 | ContextService.compress_messages |
| `DAILY_SUMMARY_PROMPT_TEMPLATE` | 每日记忆总结 | Celery generate_daily_summary |
| `MONTHLY_SUMMARY_PROMPT_TEMPLATE` | 每月记忆总结 | Celery generate_monthly_summary |
| `CRONMEM_PROMPT_TEMPLATE` | 定时记忆事实抽取与打标（参考 mem0） | cronMem 流程 |

### FR-015：上下文超限安全兜底

- 有效窗口 = max_context_window × 0.9，预留 10% 作为 buffer
- 若工具执行过程或中间过程导致上下文超过有效窗口但未超过最大窗口，利用 10% buffer 容纳
- 若超过模型最大上下文窗口（100%），直接截断内容
- 强制要求：确保任何使用场景下不会因超出模型上下文窗口而报错终止

### FR-016：前端上下文压缩状态提示

- 复用现有对话 SSE 流推送压缩状态事件，不开设独立通道
- 压缩开始时发送 `context_compacting` 事件，前端对话框左下角显示"正在压缩上下文"状态标识
- 压缩完成后发送 `context_compacted` 事件，前端移除状态标识

---

## 非功能需求

### NFR-001：性能

- 语义搜索延迟 < 500ms（pgvector 语义搜索适用宪法第五条 ES 搜索同级标准）
- 上下文裁剪/压缩引入的额外延迟 < 500ms（不含 LLM 压缩摘要的等待时间，压缩摘要为同步阻塞操作，单独计量）
- Token 计数操作延迟可忽略（tiktoken 本地计算）

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

### NFR-005：可靠性

- 上下文处理过程中任何异常不得导致对话中断
- 所有压缩/截断操作必须有兜底策略
- 确保不会因超出模型上下文窗口而报错终止

---

## 成功标准

### 可度量成果

- **SC-001**：所有对话在任何上下文大小下都能正常进行，不因超出模型窗口而中断
- **SC-002**：语义搜索在 500ms 内返回相关记忆结果
- **SC-003**：上下文压缩按正确优先级执行（d → c → b），重要内容得到保留
- **SC-004**：用户 A 的记忆在任何情况下不会出现在用户 B 的搜索结果中
- **SC-005**：记忆总结（日/月）按时执行，覆盖所有活跃用户
- **SC-006**：前端在上下文压缩期间正确显示状态提示，完成后提示消失
- **SC-007**：四个 LangGraph 流程各自使用正确的工具集，不越界

---

## 假设

- 模型上下文窗口 ≥ 10,000 tokens（在 model_config 中校验）
- 前端输入框限制最多 4k tokens
- systemPrompt 固定约 2k tokens，prompt 模板固定约 1k tokens
- Embedding 向量维度固定 2048
- tiktoken cl100k_base 编码覆盖所有支持的模型
- cronMem 的 prompt 设计参考 mem0 的实现方案

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — v2.0 整合分层上下文结构、优先级压缩策略、工具集定义、LangGraph 四流程编排、前端压缩状态提示、安全兜底机制*
