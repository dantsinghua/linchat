# 行为模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [process-model.md](process-model.md)

---

## 1. 分层上下文组装

> 交叉引用：[spec.md FR-001](spec.md#fr-001分层上下文组装结构) | [spec.md FR-002](spec.md#fr-002动态上下文窗口计算) | [spec.md FR-014](spec.md#fr-014动态-prompt-模板系统promptbuilder) | [rule-model.md R-001/R-002](rule-model.md#r-001有效上下文窗口计算) | [data-model.md §7](data-model.md#7-上下文分层结构逻辑模型非持久化)

```python
# 原子行为：计算有效上下文窗口
def get_effective_window(model_config: ModelConfig) -> int:
    """
    输入：ModelConfig 实例
    输出：有效 token 数
    规则：
      1. effective = model_config.max_context_window * 0.9
      2. 若 max_context_window < 10,000，拒绝使用并抛出异常
      3. 返回 int(effective)
    Token 计数：tiktoken 库，编码方式 cl100k_base（→ R-017）
    """

# 原子行为：分层组装上下文消息列表（PromptBuilder 核心）
def build_messages(
    user_id: int,
    user_message: str,
    model_config: ModelConfig,
    enabled_modules: list[PromptModule] | None = None,
) -> list[dict]:
    """
    输入：用户 ID、用户当前消息、模型配置、启用的功能模块列表
    输出：完整消息列表（dict 格式）
    规则：
      1. 构建层级 1：build_system_prompt() — 基础角色 + 行为规范 + 功能模块（~2k tokens）
      2. 构建层级 2.a：build_template_block() — prompt 模板固定部分（~1k tokens）
      3. 构建层级 2.b：build_memory_block() — 召回记忆（独立 system 消息）
      4. 构建层级 2.c：build_tool_context() — 工具定义
      5. 构建层级 2.d：build_conversation_history() — 历史对话
      6. 构建层级 2.e：用户当前输入（不可压缩）
      7. 固定加载层级 1 + 2.a 后，按 2.b → 2.c → 2.d → 2.e 顺序全部加载动态段，加载完成后计算总 token 数
      8. 若总 token 数超出有效窗口，按 d → c → b 顺序依次压缩（→ §2）
    """

# 原子行为：构建 LangChain 格式消息列表
def build_messages_for_langchain(
    user_id: int,
    user_message: str,
    model_config: ModelConfig,
) -> list[BaseMessage]:
    """
    输入：同 build_messages
    输出：LangChain BaseMessage 列表（SystemMessage/HumanMessage/AIMessage）
    规则：与 build_messages 逻辑一致，仅输出格式不同
    """
```

### PromptBuilder 子方法

```python
def build_system_prompt(modules: list[PromptModule]) -> str:
    """层级 1：基础角色 + 行为/推理/安全规范 + 功能模块（→ FR-014）"""

def build_template_block() -> str:
    """层级 2.a：prompt 模板固定部分"""

def build_memory_block(user_id: int, user_message: str) -> str | None:
    """层级 2.b：调用 retrieve_relevant_memories 召回记忆，格式化为 system 消息"""

def build_tool_context(tools: list) -> str | None:
    """层级 2.c：工具定义注入"""

def build_conversation_history(user_id: int, limit: int) -> list[dict]:
    """层级 2.d：从 message 表读取最近对话历史"""
```

### 功能模块注册

```python
class PromptModule(Enum):
    BASE = "base"           # 语言风格、准确性、安全隐私（始终加载）
    REASONING = "reasoning"  # 结构化思考（按需）
    TOOL_USAGE = "tool_usage"  # 工具调用规范（有工具时启用）
    CODE_ASSIST = "code_assist"  # 代码辅助（按需）
    CREATIVE_WRITING = "creative_writing"  # 创意写作（按需）
    DATA_ANALYSIS = "data_analysis"  # 数据分析（按需）

# 支持运行时动态扩展
def register_custom_module(name: str, content: str) -> None:
    """注册自定义功能模块"""
```

---

## 2. 优先级驱动的上下文压缩

> 交叉引用：[spec.md FR-003](spec.md#fr-003优先级驱动的上下文压缩) | [spec.md FR-004](spec.md#fr-004上下文工具集仅上下文处理流程使用) | [spec.md FR-005](spec.md#fr-005记忆工具集记忆处理流程--主流程均可使用) | [rule-model.md R-003/R-014](rule-model.md#r-003优先级驱动的上下文压缩) | [process-model.md §2](process-model.md#2-优先级驱动的上下文压缩流程)

```python
# 原子行为：优先级驱动的上下文压缩
async def compress_context(
    layers: ContextLayers,
    effective_window: int,
    user_id: int,
) -> ContextLayers:
    """
    输入：各层上下文内容、有效窗口 token 数、用户 ID
    输出：压缩后的各层上下文内容
    并发控制：Redis 分布式锁按 user_id 加锁
    SSE 事件：压缩开始发送 context_compacting，完成发送 context_compacted（→ R-020）
    规则：
      1. 获取 Redis 分布式锁（key=compress:{user_id}）
         - 未获锁：等待锁释放后重新检查 token 是否仍超限
      2. 发送 SSE context_compacting 事件
      3. 第一步：压缩前对话(2.d)
         - 启动 context 流程（LangGraph），输入 (1)+2.a+2.e+2.d
         - 使用上下文工具：contextCompact / contextExtract / contextPrune
         - 若输入超长直接截断
      4. 若仍超限，第二步：压缩工具内容(2.c)
         - 启动 context 流程，输入 (1)+2.a+2.e+2.c
         - 使用上下文工具
      5. 若仍超限，第三步：处理记忆内容(2.b)
         - 启动 memory 流程（LangGraph），输入 (1)+2.a+2.e+2.b
         - 使用记忆工具：memSearch / memCache / memUpdate / memDelete
      6. 若所有段处理后仍超限，直接截断超出内容
      7. 成功压缩时调用 create_memory(type='compaction') 存入记忆
      8. 发送 SSE context_compacted 事件
      9. 释放 Redis 锁
    LLM 调用失败处理（→ R-014）：
      - 重试 3 次后回退到简单截断（丢弃最早消息）
      - 回退截断不生成 compaction 记忆
    可观测性：Langfuse 追踪 LLM 调用，Django logging 记录压缩触发/失败事件
    """
```

### 上下文工具行为

```python
# contextCompact：压缩总结
async def context_compact(content: str) -> str:
    """将长对话/内容压缩成摘要"""

# contextExtract：片段抽取
async def context_extract(content: str, query: str) -> str:
    """从内容中检索抽取与 query 相关的片段"""

# contextPrune：删除剪枝
async def context_prune(content: str) -> str:
    """去除冗余内容"""
```

---

## 3. LangGraph 流程编排

> 交叉引用：[spec.md FR-007](spec.md#fr-007langgraph-流程编排) | [rule-model.md R-018](rule-model.md#r-018langgraph-流程工具集隔离) | [process-model.md §1](process-model.md#1-langgraph-四流程编排总览)

```python
# 原子行为：chat 流程（主对话）
async def run_chat_flow(
    messages: list[dict],
    tools: list,
) -> AsyncGenerator[str, None]:
    """
    输入：完整上下文消息列表（1 + 2.a~2.e）
    工具集：记忆工具（本期）+ python repl + bravo search + home assistant（后续特性预留，本期仅记忆工具）
    流程：整个上下文 → Agent → Tool → End
    输出：SSE 流式响应
    """

# 原子行为：context 流程（上下文处理）
async def run_context_flow(
    system_tokens: str,
    template_tokens: str,
    user_input: str,
    target_content: str,  # 2.c 或 2.d 的内容
) -> str:
    """
    输入：(1)tokens + 2.a tokens + 2.e tokens + 对应内容(2.c 或 2.d)
    工具集：仅上下文工具（contextCompact / contextExtract / contextPrune）
    流程：输入上下文 → Agent → Tool → End
    规则：若传入内容超长，直接截断处理
    输出：处理后的内容
    """

# 原子行为：memory 流程（记忆处理）
async def run_memory_flow(
    system_tokens: str,
    template_tokens: str,
    user_input: str,
    memory_content: str,  # 2.b 的内容
) -> str:
    """
    输入：(1)tokens + 2.a tokens + 2.e tokens + 2.b
    工具集：仅记忆工具（memSearch / memCache / memUpdate / memDelete）
    流程：输入上下文 → Agent → Tool → End
    规则：若传入内容超长，直接截断处理
    输出：处理后的记忆内容
    """

# 原子行为：cronMem 流程（定时记忆总结）
async def run_cronmem_flow(
    memory_content: str,
    user_id: int,
) -> dict:
    """
    输入：专用 system prompt（参考 mem0 prompt 设计）+ 对应记忆内容
    工具集：无（仅 Agent → End）
    流程：专用 prompt + 记忆内容 → Agent → End
    输出：dict 包含 content、tags、更新 date 等
    LLM 调用失败处理（→ R-022）：重试 3 次后跳过该用户
    """
```

**工具选择策略**：每个流程中 Agent（LLM）根据 system prompt 中的工具使用规范（PromptModule.TOOL_USAGE）自主决定调用哪些注册工具，系统仅通过流程级工具集隔离（→ R-018）限制可用工具范围，不做预定义调用序列。

---

## 4. 记忆工具行为

> 交叉引用：[spec.md FR-005](spec.md#fr-005记忆工具集记忆处理流程--主流程均可使用)

```python
# memSearch：记忆查询
async def mem_search(user_id: int, query: str, limit: int = 5) -> list[dict]:
    """
    混合检索（关键字 + 向量检索）PostgreSQL 中的记忆表数据
    返回最多 5 条结果，按向量相似度（权重 0.7）与关键词匹配（权重 0.3）的加权得分排序
    关键词匹配实现：PostgreSQL 全文检索（tsvector + GIN 索引 + pg_jieba 中文分词）
    embedding_status != 'done' 的记录退化为关键词匹配（→ R-005）
    """

# memCache：记忆存储
async def mem_cache(user_id: int, content: str, name: str | None = None) -> dict:
    """将用户强调的内容写入 user_memory 表，之后定时同步到向量库"""

# memUpdate：记忆更新
async def mem_update(user_id: int, memory_ids: list[int], updates: dict) -> list[dict]:
    """将一至多个记忆进行数据更新操作"""

# memDelete：记忆删除
async def mem_delete(user_id: int, memory_ids: list[int]) -> bool:
    """将一至多个记忆进行删除操作"""
```

---

## 5. 记忆 CRUD

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆-crud) | [spec.md FR-009](spec.md#fr-009用户记忆隔离) | [rule-model.md R-004](rule-model.md#r-004用户记忆隔离不可违背) | [rule-model.md R-009](rule-model.md#r-009事务保护) | [rule-model.md R-021](rule-model.md#r-021content-最大长度) | [data-model.md §2](data-model.md#2-表-1user_memory记忆元数据)

```python
# 原子行为：创建记忆
async def create_memory(user_id: int, content: str, type: str = "memory", name: str | None = None) -> Memory:
    """
    规则：
      1. content 最大 10,000 字符，超出拒绝（→ R-021）
      2. 用户 REST API 创建 type 固定为 'memory'，不接受客户端指定（→ R-008）
      3. 事务中写入 user_memory，embedding_status = 'pending', retry_count = 0
      4. 投递异步任务生成 embedding
         - Embedding 服务不可用时：元数据正常写入，标记 failed，不阻塞（→ R-015）
      5. 返回记忆实体
    user_id 来源：视图层从 request.user.user_id 注入（→ R-004）
    """

# 原子行为：更新记忆
async def update_memory(memory_id: int, user_id: int, content: str) -> Memory:
    """
    规则：
      1. 验证 memory_id 属于 user_id（隔离检查，→ R-004）
      2. content 最大 10,000 字符，超出拒绝（→ R-021）
      3. 事务中更新 user_memory，embedding_status 重置为 'pending', retry_count 重置为 0
      4. 投递异步任务重新生成 embedding
    """

# 原子行为：删除记忆
async def delete_memory(memory_id: int, user_id: int) -> bool:
    """
    规则：
      1. 验证 memory_id 属于 user_id（隔离检查，→ R-004）
      2. 删除 user_memory 记录（级联删除 embedding）
    """

# 原子行为：语义搜索
async def search_memory(user_id: int, query: str, limit: int = 5) -> list[Memory]:
    """
    规则：
      1. user_id 必须提供，否则抛出异常（→ R-004）
      2. 生成 query 的 embedding
      3. vector_search：在 user_memory_embedding 中按 user_id 过滤、embedding_status='done' 的记录做向量相似度搜索，返回 (memory_id, vector_score)
      4. keyword_search：在 user_memory 中按 user_id 过滤、所有记录（不限 embedding_status）做全文检索（tsvector + GIN + pg_jieba），返回 (memory_id, keyword_score)
      5. 合并：同一 memory_id 出现在两个结果中时 final_score = vector_score × 0.7 + keyword_score × 0.3；仅出现在 vector_search 中时 final_score = vector_score × 0.7；仅出现在 keyword_search 中时 final_score = keyword_score × 0.3。去重后按 final_score 降序取 top 5
    性能约束：延迟 < 500ms（→ R-010）
    """
```

---

## 6. 记忆自动召回

> 交叉引用：[spec.md FR-010](spec.md#fr-010向量检索) | [spec.md 用户故事 4](spec.md#用户故事-4--语义搜索与自动召回优先级p1)

```python
# 原子行为：对话前自动召回相关记忆
async def retrieve_relevant_memories(user_id: int, user_message: str, limit: int = 5) -> list[Memory]:
    """
    输入：用户 ID、用户消息内容
    输出：相关记忆列表
    规则：
      1. 调用 search_memory 进行混合检索
      2. 将结果格式化为上下文注入格式
      3. 注入位置：层级 2.b — system prompt(1) 和模板(2.a) 之后、工具内容(2.c) 之前
    """
```

---

## 7. 核心总结方法

> 交叉引用：[spec.md FR-012](spec.md#fr-012记忆总结) | [rule-model.md R-007](rule-model.md#r-007记忆总结数据来源降级) | [rule-model.md R-022](rule-model.md#r-022cronmem-llm-调用失败) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

```python
# 原子行为：总结并存储（每日/每月共用）
async def summarize_and_store(
    user_id: int,
    content: str | list[str],
    summary_type: str,
    name: str,
) -> Memory:
    """
    输入：用户 ID、待总结内容、总结类型、名称
    输出：Memory 实体
    规则：
      1. 调用 cronMem 流程（LangGraph）进行事实抽取和记忆打标
      2. cronMem 使用专用 system prompt（参考 mem0 prompt 设计）
      3. 输出 content、tags、更新 date 等
      4. 调用 create_memory 写入（type = summary_type）
      5. 异步生成 embedding
    活跃用户定义（→ R-007）：
      - 每日总结：当天有新 compaction 记忆或新 message 记录的用户
      - 每月总结：当月有 daily-summary 记忆或新 message 记录的用户
    LLM 调用失败处理（→ R-022）：重试 3 次后跳过该用户，记录 WARNING 日志
    可观测性：Langfuse 追踪 LLM 调用，Django logging 记录总结执行事件（→ R-016）
    """
```

### 专用 Prompt 模板

| 模板 | 用途 | 调用方 |
|------|------|--------|
| `COMPACTION_PROMPT_TEMPLATE` | 对话压缩摘要生成 | compress_context（context 流程） |
| `DAILY_SUMMARY_PROMPT_TEMPLATE` | 每日记忆总结 | Celery generate_daily_summary |
| `MONTHLY_SUMMARY_PROMPT_TEMPLATE` | 每月记忆总结 | Celery generate_monthly_summary |
| `CRONMEM_PROMPT_TEMPLATE` | 定时记忆事实抽取与打标（参考 mem0） | cronMem 流程 |

> **调用关系说明**：`COMPACTION_PROMPT_TEMPLATE` 不是 context 流程 Agent 的 system prompt，而是 `compress_context` 编排完成后的独立 LLM 调用——将压缩后的对话内容通过此模板生成 compaction 摘要，再调用 `create_memory(type='compaction')` 存入。context 流程 Agent 使用通用 system prompt + 上下文工具集进行压缩处理，两者是不同的 LLM 调用。

---

## 8. Embedding 异步生成（含重试）

> 交叉引用：[rule-model.md R-011](rule-model.md#r-011embedding-模型配置) | [rule-model.md R-013](rule-model.md#r-013embedding-重试上限) | [rule-model.md R-015](rule-model.md#r-015embedding-服务不可用降级) | [data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

```python
# 原子行为：生成 embedding（Celery 异步任务）
async def generate_embedding(memory_id: int) -> None:
    """
    规则：
      1. 将 embedding_status 更新为 'processing'
      2. 从 model 表获取 type='embedding' 的配置（→ R-011）
         - 若无配置，抛出 EmbeddingConfigNotFoundError，status → 'failed'
      3. 调用 OpenAI API 兼容接口生成向量
      4. 校验向量维度 = 2048，非 2048 报错
      5. content token 数超出模型输入限制时，截取前 N tokens 生成 embedding（→ R-021）
      6. 成功：写入 user_memory_embedding，status → 'done'
      7. 失败：retry_count += 1
         - retry_count < 3：status → 'failed'，等待定时任务重试
         - retry_count >= 3：status 保持 'failed'，永久退化为关键词匹配
    可观测性：Django logging 记录失败事件和重试耗尽事件（→ R-016）
    """
```

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*更新日期：2026-01-31 — v2.2 F7 修正组装顺序描述（明确 1+2.a 固定先加载），F12 补充 Agent 工具选择策略说明*
