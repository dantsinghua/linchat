# 行为模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [process-model.md](process-model.md)

---

## 1. 上下文窗口计算

> 交叉引用：[spec.md FR-001](spec.md#fr-001动态上下文窗口计算) | [rule-model.md R-001](rule-model.md#r-001有效上下文窗口计算) | [rule-model.md R-017](rule-model.md#r-017token-计数方式)

```python
# 原子行为：获取有效上下文窗口
def get_effective_window(model_config: ModelConfig) -> int:
    """
    输入：ModelConfig 实例
    输出：有效 token 数
    规则：
      1. effective = model_config.max_context_window * 0.9
      2. 返回 int(effective)
    注：可直接复用 ModelConfig.effective_context_window 属性
    Token 计数：tiktoken 库，编码方式 cl100k_base
    """
```

## 2. 渐进式上下文裁剪

> 交叉引用：[spec.md FR-002](spec.md#fr-002渐进式上下文裁剪) | [rule-model.md R-002](rule-model.md#r-002裁剪保留规则) | [process-model.md §2](process-model.md#2-上下文裁剪与压缩流程)

```python
# 原子行为：裁剪消息以适应上下文窗口
def prune_messages(
    messages: list[Message],
    max_tokens: int,
    keep_recent_rounds: int = 2,
) -> tuple[list[Message], list[Message]]:
    """
    输入：完整消息列表（含 system prompt、召回记忆、历史消息、用户输入）、有效窗口 token 数、保留轮数
    输出：(保留的消息, 被裁剪的消息)
    Token 计数：tiktoken 库，编码方式 cl100k_base
    术语：1 轮 = 1 条 role=user 消息 + 1 条 role=assistant 消息（保留 2 轮 = 保留最后 4 条 user/assistant 消息）
    规则：
      1. 分离 system 消息和非 system 消息
      2. 计算必须保留的消息：system prompt + 召回记忆 + 最近 N 轮 + 用户当前输入
      3. 从最早的非保留消息开始丢弃，直到完整 prompt token 总量 < 有效窗口
      4. 如果全部丢弃后仍超限，返回被裁剪列表（交由压缩处理）
    """
```

## 3. Safeguard 压缩

> 交叉引用：[spec.md FR-003](spec.md#fr-003safeguard-压缩) | [rule-model.md R-003](rule-model.md#r-003压缩触发条件) | [rule-model.md R-014](rule-model.md#r-014llm-压缩失败回退) | [process-model.md §2](process-model.md#2-上下文裁剪与压缩流程)

```python
# 原子行为：分块摘要压缩
async def compress_messages(
    messages_to_compress: list[Message],
    user_id: int,
) -> Message:
    """
    输入：需要压缩的消息列表、用户 ID
    输出：摘要消息（替换原始消息）
    副作用：将原始内容存入 user_memory 表（type='compaction'）
    并发控制：Redis 分布式锁按 user_id 加锁
    规则：
      1. 获取 Redis 分布式锁（key=compress:{user_id}）
         - 未获锁：等待锁释放后重新检查 token 是否仍超限
      2. 将消息格式化为文本
      3. 调用 LLM 生成摘要
         - LLM 调用失败：重试 3 次
         - 3 次全部失败：回退到简单截断（丢弃最早消息），不生成 compaction 记忆
      4. 成功时：创建 system 消息包含摘要
      5. 调用 create_memory(type='compaction', content=摘要文本) 写入记忆（不经过 summarize_and_store，摘要已在压缩步骤生成）
      6. 释放 Redis 锁
    可观测性：Langfuse 追踪 LLM 调用，Django logging 记录压缩触发/失败事件
    """
```

## 4. 记忆 CRUD

> 交叉引用：[spec.md FR-004](spec.md#fr-004记忆-crud) | [rule-model.md R-004](rule-model.md#r-004用户记忆隔离不可违背) | [rule-model.md R-009](rule-model.md#r-009事务保护) | [data-model.md §2](data-model.md#2-表-1user_memory记忆元数据)

```python
# 原子行为：创建记忆
async def create_memory(user_id: int, content: str, type: str, name: str | None) -> Memory:
    """
    规则：
      1. 事务中写入 user_memory，embedding_status = 'pending', retry_count = 0
      2. 投递异步任务生成 embedding
         - Embedding 服务不可用时：元数据正常写入，标记 failed，不阻塞（→ R-015）
      3. 返回记忆实体
    """

# 原子行为：更新记忆
async def update_memory(memory_id: int, user_id: int, content: str) -> Memory:
    """
    规则：
      1. 验证 memory_id 属于 user_id（隔离检查，→ R-004）
      2. 事务中更新 user_memory，embedding_status 重置为 'pending', retry_count 重置为 0
      3. 投递异步任务重新生成 embedding
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
      3. 在 user_memory_embedding 中按 user_id 过滤后做向量相似度搜索
      4. embedding_status != 'done' 的记录退化为关键词匹配（→ R-005）
      5. 合并结果，去重，按相似度排序
    性能约束：延迟 < 500ms（→ R-010）
    """
```

## 5. 记忆自动召回

> 交叉引用：[spec.md FR-006](spec.md#fr-006向量检索) | [spec.md 用户故事 3](spec.md#用户故事-3--语义搜索与自动召回优先级p1) | [process-model.md §1](process-model.md#1-对话处理主流程含上下文管理与记忆召回)

```python
# 原子行为：对话前自动召回相关记忆
async def retrieve_relevant_memories(user_id: int, user_message: str, limit: int = 5) -> list[Memory]:
    """
    输入：用户 ID、用户消息内容
    输出：相关记忆列表
    规则：
      1. 调用 search_memory 进行语义搜索
      2. 将结果格式化为上下文注入格式
      3. 注入位置：system prompt 之后、历史消息之前（→ spec.md FR-002）
    """
```

## 6. 核心总结方法

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆总结) | [rule-model.md R-007](rule-model.md#r-007记忆总结数据来源降级) | [process-model.md §4](process-model.md#4-记忆总结定时任务流程)

```python
# 原子行为：总结并存储（压缩/每日/每月共用）
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
      1. 调用 LLM 生成摘要
      2. 调用 create_memory 写入（type = summary_type）
      3. 异步生成 embedding
    可观测性：Langfuse 追踪 LLM 调用，Django logging 记录总结执行事件（→ R-016）
    """
```

## 7. Embedding 异步生成（含重试）

> 交叉引用：[rule-model.md R-013](rule-model.md#r-013embedding-重试上限) | [rule-model.md R-015](rule-model.md#r-015embedding-服务不可用降级) | [data-model.md §4](data-model.md#4-embedding_status-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

```python
# 原子行为：生成 embedding（Celery 异步任务）
async def generate_embedding(memory_id: int) -> None:
    """
    规则：
      1. 将 embedding_status 更新为 'processing'
      2. 从 model 表获取 embedding 模型配置（→ R-011）
      3. 调用 OpenAI API 兼容接口生成向量
      4. 成功：写入 user_memory_embedding，status → 'done'
      5. 失败：retry_count += 1
         - retry_count < 3：status → 'failed'，等待定时任务重试
         - retry_count >= 3：status 保持 'failed'，永久退化为关键词匹配
    可观测性：Django logging 记录失败事件和重试耗尽事件（→ R-016）
    """
```

---

*文档版本：v1.1*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — 修复 session_id→conversation_id、补充 Redis 锁/LLM 失败回退/重试上限/可观测性、新增 §7 Embedding 异步生成、全文交叉引用、analyze 修复（去16k下限/裁剪改完整prompt/conversation_id→user_id/compress调create_memory/user_id改int）*
