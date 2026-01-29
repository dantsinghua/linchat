# M1b: 上下文与记忆管理 - 需求规划文档

## 1. 概述

### 1.1 背景
linchat 当前对话框硬编码最大输入 4000 token，无长期记忆能力。需要构建动态上下文窗口管理和数据库化的长期记忆系统。

### 1.2 目标
- 动态上下文窗口管理（基于 model 表的 max_context_window * 90%）
- 上下文裁剪与压缩（safeguard 模式）
- 长期记忆 CRUD（PostgreSQL + pgvector）
- 用户记忆隔离
- 记忆总结机制（压缩/每日/每月）

### 1.3 前置依赖
- M1a 完成：model 表可用，能读取模型的 max_context_window

### 1.4 参考实现
- `moltbot/src/agents/context.ts` — 模型上下文窗口查找
- `moltbot/src/agents/context-window-guard.ts` — 窗口大小解析与安全守卫
- `moltbot/src/agents/compaction.ts` — 分块摘要压缩策略
- `moltbot/src/memory/` — 整体记忆架构
- `moltbot/src/agents/memory-search.ts` — 搜索配置与策略

---

## 2. 功能需求

### 2.1 上下文窗口管理（Context Window Management）

**核心逻辑：**
1. 从 `model` 表读取当前语言模型的 `max_context_window`
2. 取 **90%** 作为 Agent 可用的上下文窗口（预留 10% 作为 buffer）
3. 基于此窗口实现上下文裁剪与压缩策略

```python
effective_context_window = model.max_context_window * 0.9
```

**关键能力：**

| 能力 | 说明 |
|------|------|
| 动态窗口计算 | 从模型表实时获取，不硬编码 |
| 上下文裁剪 | 超出窗口时自动裁剪历史消息（从最早的开始丢弃） |
| 上下文压缩 | 支持 safeguard 模式的分块摘要压缩 |
| 缓存管理 | cache-ttl 模式的上下文缓存 |

**实现要点：**
- 上下文窗口来源优先级：model 表 > 默认值
- 硬性下限：16,000 tokens（防止配置错误）
- 压缩策略：分块摘要 + 合并（参考 moltbot safeguard 模式）

**裁剪策略详解：**
1. **渐进式裁剪**：当 token 总数接近窗口上限时，优先丢弃最早的非 system 消息
2. **保底规则**：始终保留最近 N 轮对话（N 可配置，默认 2）+ system prompt + 召回的记忆
3. **触发压缩**：如果裁剪后仍然超限，触发 safeguard 压缩（调用 LLM 将被裁剪的消息生成摘要，同时写入记忆表）

**压缩策略详解（safeguard 模式）：**
```python
async def compress_context(messages: List[Message], max_tokens: int) -> List[Message]:
    """
    1. 计算当前 token 总量
    2. 如果超限：
       a. 取出最早的 N 条消息
       b. 调用 LLM 生成摘要
       c. 用摘要替换原始消息
       d. 将压缩的原始内容存入记忆表（type='compaction'）
    3. 重复直到 token 总量 < max_tokens
    """
```

---

### 2.2 长期记忆管理（Memory Management）

**核心原则：**
- 所有记忆以 **PostgreSQL 表** 存储和管理（不是文件系统）
- 所有操作为数据库 CRUD
- 数据操作保证**原子性**（事务管理）
- **必须确保用户记忆隔离**（用户 A 的记忆不能被用户 B 访问）

#### 2.2.1 数据模型

**表 1：`user_memory`（记忆元数据）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | 主键 |
| user_id | VARCHAR NOT NULL | 用户标识 |
| type | VARCHAR NOT NULL | 类型：`memory` / `image` / `file` / `audio` / `video`（先实现 `memory`） |
| name | VARCHAR | 名称 |
| content | TEXT | 原始记忆文本 |
| embedding_status | VARCHAR | `pending` / `processing` / `done` / `failed` |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

**表 2：`user_memory_embedding`（记忆向量）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | 主键 |
| memory_id | INTEGER FK | 关联 user_memory.id |
| user_id | VARCHAR NOT NULL | 用户标识（冗余，加速查询） |
| type | VARCHAR NOT NULL | 类型（冗余） |
| name | VARCHAR | 名称（冗余） |
| chunk_index | INTEGER | 分块序号 |
| chunk_text | TEXT | 分块文本 |
| embedding | VECTOR | pgvector 向量 |
| created_at | TIMESTAMP | 创建时间 |

#### 2.2.2 关键能力

| 能力 | 说明 |
|------|------|
| 记忆 CRUD | 完整的增删改查操作 |
| 向量检索 | 基于 pgvector 的语义搜索 |
| 用户隔离 | 所有查询必须带 `user_id` 过滤，**必须有测试覆盖** |
| 原子性 | 创建/更新记忆时，元数据和 embedding 在同一事务中完成 |
| 自动召回 | 回复前自动检索相关记忆注入上下文 |

#### 2.2.3 Embedding 模型要求

- 只需支持 **OpenAI API 兼容接口**（方便后续使用自部署模型服务）
- 从 `model` 表获取 embedding 模型配置（URL、API Key、维度等）

#### 2.2.4 用户隔离测试要求

```python
def test_memory_isolation():
    """用户 A 创建的记忆，用户 B 搜索时不能获取到"""

def test_memory_search_with_user_filter():
    """搜索时必须携带 user_id，否则报错"""

def test_concurrent_memory_operations():
    """并发创建/更新记忆时数据一致性"""
```

---

### 2.3 数据同步机制（`user_memory` ↔ `user_memory_embedding`）

两表数据**必须最终一致**，允许短暂延迟但不允许不一致。

**方案：以 SQL 表为主表，embedding 表为从表**

| 操作 | SQL 表 | Embedding 表 | 一致性保证 |
|------|--------|-------------|-----------|
| 创建 | 先写入 `user_memory` | 异步生成 embedding 写入 | `embedding_status`: `pending` → `done` |
| 更新 | 先更新 `user_memory` | 异步重新生成 embedding | 旧 embedding 标记失效 → 新 embedding 写入 → 删除旧数据 |
| 删除 | 先删除 `user_memory` | 级联删除（FK ON DELETE CASCADE） | 数据库级联保证 |
| 查询 | 直接查询 | 仅在语义搜索时使用 | — |

**同步保障：**
1. 创建/更新记忆后，投递异步任务生成 embedding
2. 定时扫描 `embedding_status = 'failed'` 或 `'pending'` 超时的记录，自动重试
3. 语义搜索时，`embedding_status != 'done'` 的记录退化为关键词匹配

---

### 2.4 记忆总结机制

总结分为 **主动触发** 和 **定时触发** 两种形式，共用同一个核心总结方法。

**触发方式：**

| 触发方式 | 时机 | 数据来源 |
|---------|------|---------|
| 主动触发（压缩） | 对话触发上下文压缩时 | 当前被压缩的会话内容 → 写入记忆 SQL 表（type=`compaction`） |
| 每日总结 | 每天 00:00 | 优先取当天压缩记忆，若无则取 message 表原始会话 |
| 每月总结 | 每月 1 日 00:00 | 优先取每日摘要，若无则取 message 表原始会话 |

**数据来源降级策略（每日/每月总结通用）：**
1. 首先查 `user_memory` 表，按类型取对应记录
2. 若无记录 → 降级到 `message` 表取该用户的原始对话
3. 若仍无记录 → **跳过**，不生成空总结

**核心总结方法（共用）：**
```python
async def summarize_and_store(
    user_id: str,
    content: str | List[str],
    summary_type: str,         # 'compaction' | 'daily-summary' | 'monthly-summary'
    name: str,                 # 如 'compaction-{session_id}', 'daily-2026-01-29', 'monthly-2026-01'
) -> Memory:
    """
    核心总结方法，压缩/每日/每月总结共用。
    1. 调用 LLM 生成摘要
    2. 写入 user_memory 表
    3. 异步生成 embedding
    """
    summary = await llm.summarize(content)
    return await memory_service.create(user_id, summary, type=summary_type, name=name)
```

**各触发场景调用：**
```python
# 1. 对话压缩时主动触发
async def on_context_compaction(user_id: str, session_id: str, compacted_messages: List[Message]):
    content = format_messages(compacted_messages)
    await summarize_and_store(user_id, content, summary_type="compaction", name=f"compaction-{session_id}-{now}")

# 2. 每日总结（定时）
async def daily_memory_summary(user_id: str):
    records = await memory_service.list(user_id, type="compaction", created_after=today_start)
    if not records:
        records = await message_service.get_user_conversations(user_id, since=today_start)
    if not records:
        return  # 无数据，跳过
    await summarize_and_store(user_id, [r.content for r in records], summary_type="daily-summary", name=f"daily-{today}")

# 3. 每月总结（定时），逻辑同上
async def monthly_memory_summary(user_id: str):
    records = await memory_service.list(user_id, type="daily-summary", created_after=month_start)
    if not records:
        records = await message_service.get_user_conversations(user_id, since=month_start)
    if not records:
        return
    await summarize_and_store(user_id, [r.content for r in records], summary_type="monthly-summary", name=f"monthly-{month}")
```

**记忆类型：**

| type 值 | 来源 | 说明 |
|--------|------|------|
| `memory` | 用户/Agent 主动创建 | 通用记忆 |
| `compaction` | 对话压缩自动触发 | 单次压缩的会话摘要 |
| `daily-summary` | 每日定时任务 | 当天所有对话/压缩的汇总 |
| `monthly-summary` | 每月定时任务 | 当月所有每日摘要的汇总 |
| `image` | （后续扩展） | 图片记忆 |
| `file` | （后续扩展） | 文件记忆 |
| `audio` | （后续扩展） | 音频记忆 |
| `video` | （后续扩展） | 视频记忆 |

---

## 3. 技术架构

### 3.1 数据库设计

```sql
CREATE TABLE user_memory (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL DEFAULT 'memory',
    name VARCHAR(200),
    content TEXT NOT NULL,
    embedding_status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_user_memory_user_id ON user_memory(user_id);
CREATE INDEX idx_user_memory_embedding_status ON user_memory(embedding_status);
CREATE INDEX idx_user_memory_type ON user_memory(user_id, type);

CREATE TABLE user_memory_embedding (
    id SERIAL PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES user_memory(id) ON DELETE CASCADE,
    user_id VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL,
    name VARCHAR(200),
    chunk_index INTEGER DEFAULT 0,
    chunk_text TEXT,
    embedding VECTOR,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_user_memory_embedding_user ON user_memory_embedding(user_id);
CREATE INDEX idx_user_memory_embedding_memory ON user_memory_embedding(memory_id);
```

### 3.2 LangGraph 状态定义

```python
class AgentState(TypedDict):
    messages: Annotated[List[Message], add]
    max_context_tokens: int       # model.max_context_window * 0.9
    retrieved_memories: List[Memory]
    session_id: str
    user_id: str
    timestamp: float
```

### 3.3 节点结构

```
[Input] 
    → [Memory Retrieval]      ← 基于 user_id 隔离查询
    → [Context Management]    ← 动态窗口 = model.max_context_window * 0.9
    → [LLM Call]              ← 从 model 表获取配置
    → [Response Generation]
    → [Memory Storage]        ← 写入 user_memory + user_memory_embedding
    → [Output]
```

---

## 4. 接口定义

### 4.1 Memory Service Interface

```python
class MemoryService:
    async def create(self, user_id: str, content: str, type: str = "memory", name: str = None) -> Memory:
        """创建记忆（事务：同时写入元数据 + 异步 embedding）"""
    
    async def update(self, memory_id: int, user_id: str, content: str) -> Memory:
        """更新记忆（事务：更新元数据 + 重新生成 embedding）"""
    
    async def delete(self, memory_id: int, user_id: str) -> bool:
        """删除记忆（级联删除 embedding）"""
    
    async def search(self, user_id: str, query: str, limit: int = 5) -> List[Memory]:
        """语义搜索记忆（必须带 user_id 过滤）"""
    
    async def list(self, user_id: str, type: str = None) -> List[Memory]:
        """列出用户记忆"""
```

### 4.2 Context Service Interface

```python
class ContextService:
    def __init__(self, model_service: ModelService): ...
    
    def get_effective_window(self, model_id: str) -> int:
        """获取有效上下文窗口（max_context_window * 0.9，最低 16000）"""
    
    def prune(self, messages: List[Message], max_tokens: int) -> List[Message]:
        """裁剪消息以适应上下文窗口"""
    
    async def compress(self, messages: List[Message], user_id: str, session_id: str) -> List[Message]:
        """分块摘要压缩（safeguard 模式），压缩内容同时写入记忆表"""
```

---

## 5. 验收标准

### 5.1 上下文管理
- [ ] 上下文窗口动态取模型表的 `max_context_window * 90%`
- [ ] 硬性下限 16,000 tokens 生效
- [ ] 超限时自动裁剪，保留最近 N 轮对话
- [ ] 裁剪不够时触发 safeguard 压缩
- [ ] 压缩内容自动写入记忆表（type='compaction'）

### 5.2 长期记忆
- [ ] 记忆 CRUD 完整（创建 / 读取 / 更新 / 删除）
- [ ] 语义搜索可用，检索延迟 < 500ms
- [ ] **用户隔离通过测试**（用户 A 搜索不到用户 B 的记忆）
- [ ] 并发操作数据一致性通过测试
- [ ] Embedding 模型支持 OpenAI API 兼容接口
- [ ] 两表数据同步机制正常（embedding_status 流转正确）

### 5.3 记忆总结
- [ ] 对话压缩时自动生成 compaction 记忆
- [ ] 每日总结定时任务可用
- [ ] 每月总结定时任务可用
- [ ] 降级策略正确（无压缩记忆时从 message 表取数据）

### 5.4 可测试性
- [ ] 记忆隔离有专门的测试用例
- [ ] 每个模块有独立的单元测试

---

## 6. 依赖与风险

### 6.1 依赖
- M1a 完成（model 表可用）
- PostgreSQL + pgvector 扩展
- OpenAI API 兼容的 Embedding 服务

### 6.2 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 记忆检索慢 | 响应延迟 | pgvector 索引优化 + 查询缓存 |
| 上下文溢出 | 对话断裂 | 渐进式裁剪 + safeguard 摘要 |
| Embedding 服务不可用 | 记忆写入降级 | 状态标记 + 定时重试 |

---

## 7. 排期建议

| 阶段 | 内容 | 预估时间 |
|------|------|----------|
| Phase 1 | 上下文动态窗口 + 裁剪 + 压缩 | 2-3 天 |
| Phase 2 | 记忆表 + CRUD + 用户隔离 | 2-3 天 |
| Phase 3 | 向量检索 + embedding 同步 | 1-2 天 |
| Phase 4 | 记忆总结机制（压缩/每日/每月） | 1-2 天 |
| Phase 5 | 集成测试与调优 | 1 天 |

**总计：约 7-11 天**

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*作者：小鱼*
