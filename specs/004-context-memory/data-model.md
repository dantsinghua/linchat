# 数据模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md FR-004](spec.md#fr-004记忆-crud) | [rule-model.md R-005/R-006](rule-model.md#r-005embedding-状态流转) | [process-model.md §3/§5](process-model.md#3-记忆-embedding-异步处理流程) | [behavior-model.md §4](behavior-model.md#4-记忆-crud)

## 1. 实体概览

```
┌──────────────────┐       ┌──────────────────────────┐
│   user_memory    │ 1───N │  user_memory_embedding   │
│  （记忆元数据表）  │       │    （记忆向量表）          │
└──────────────────┘       └──────────────────────────┘
        │                            │
        └── user_id ─────────────────┘  （冗余，加速查询）
```

> 两表通过 `memory_id` 外键关联，`user_id` 冗余存储于从表以加速按用户过滤的向量查询。

---

## 2. 表 1：`user_memory`（记忆元数据）

| 字段 | 类型 | 约束 | 说明 | 必填 |
|------|------|------|------|------|
| id | SERIAL PK | 自增主键 | — | 自动 |
| user_id | BIGINT | NOT NULL, INDEX | 用户标识（对应 SysUser.user_id，BigAutoField） | **必填** |
| type | VARCHAR(20) | NOT NULL, DEFAULT 'memory' | 类型：`memory` / `compaction` / `daily-summary` / `monthly-summary`（→ [rule-model.md R-008](rule-model.md#r-008记忆类型约束)） | **必填** |
| name | VARCHAR(200) | NULL | 名称，如 `daily-2026-01-29` | 选填 |
| content | TEXT | NOT NULL | 原始记忆文本 | **必填** |
| embedding_status | VARCHAR(20) | DEFAULT 'pending' | 状态：`pending` / `processing` / `done` / `failed`（→ [rule-model.md R-005](rule-model.md#r-005embedding-状态流转)） | 系统 |
| retry_count | INTEGER | DEFAULT 0 | embedding 生成重试计数，上限 3 次（→ [rule-model.md R-013](rule-model.md#r-013embedding-重试上限)） | 系统 |
| tags | JSONB | NULL | 预留字段，暂不使用 | 选填 |
| importance_score | FLOAT | NULL | 预留字段，暂不使用 | 选填 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 | 自动 |
| updated_at | TIMESTAMP | NOT NULL, auto_now | 更新时间 | 自动 |

### 索引

| 索引名 | 字段 | 用途 |
|--------|------|------|
| idx_user_memory_user_id | user_id | 按用户查询 |
| idx_user_memory_embedding_status | embedding_status | 扫描待处理记录 |
| idx_user_memory_type | (user_id, type) | 按用户+类型查询 |
| idx_user_memory_retry | (embedding_status, retry_count) | 扫描可重试记录 |

---

## 3. 表 2：`user_memory_embedding`（记忆向量）

| 字段 | 类型 | 约束 | 说明 | 必填 |
|------|------|------|------|------|
| id | SERIAL PK | 自增主键 | — | 自动 |
| memory_id | INTEGER FK | NOT NULL, REFERENCES user_memory(id) ON DELETE CASCADE | 关联记忆元数据 | **必填** |
| user_id | BIGINT | NOT NULL, INDEX | 用户标识（冗余，对应 SysUser.user_id，→ [rule-model.md R-004](rule-model.md#r-004用户记忆隔离不可违背)） | **必填** |
| type | VARCHAR(20) | NOT NULL | 类型（冗余） | **必填** |
| name | VARCHAR(200) | NULL | 名称（冗余） | 选填 |
| chunk_index | INTEGER | DEFAULT 0 | 分块序号（本期不实现分块，始终为 0） | 系统 |
| chunk_text | TEXT | NULL | 分块文本（本期存储完整内容，分块能力预留至后续版本） | 选填 |
| embedding | VECTOR(2048) | NULL | pgvector 向量，维度固定 2048（→ [rule-model.md R-011](rule-model.md#r-011embedding-模型配置)） | 系统 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 | 自动 |

### 索引

| 索引名 | 字段 | 用途 |
|--------|------|------|
| idx_user_memory_embedding_user | user_id | 按用户向量检索 |
| idx_user_memory_embedding_memory | memory_id | 按记忆 ID 查询 |

> 向量索引（IVFFlat 或 HNSW）在数据量增长后按需添加。

---

## 4. embedding_status 状态流转

> 交叉引用：[rule-model.md R-005](rule-model.md#r-005embedding-状态流转) | [process-model.md §3](process-model.md#3-记忆-embedding-异步处理流程)

```
创建记忆 → pending
         ↓
   异步任务开始 → processing
                  ↓
           成功 → done
           失败 → failed → 定时重试(retry_count < 3) → processing → ...
                         → 永久失败(retry_count >= 3) → 保持 failed，退化为关键词匹配
```

---

## 5. 数据同步规则

> 交叉引用：[rule-model.md R-006](rule-model.md#r-006两表一致性) | [process-model.md §5](process-model.md#5-两表数据同步时序)

| 操作 | user_memory | user_memory_embedding | 一致性保证 |
|------|------------|----------------------|-----------|
| 创建 | 先写入 | 异步生成 embedding | status: pending → done |
| 更新 | 先更新 | 异步重新生成 | 旧数据标记失效 → 新数据写入 → 删除旧数据 |
| 删除 | 先删除 | 级联删除（FK CASCADE） | 数据库级联保证 |
| 查询 | 直接查询 | 仅语义搜索时使用 | — |

---

## 6. 与现有表关系

- **SysUser**：通过 `user_id` 关联（逻辑外键，不建物理 FK）
- **ModelConfig**：ContextService 从 `model` 表读取 `max_context_window` 计算有效窗口（→ [rule-model.md R-001](rule-model.md#r-001有效上下文窗口计算)）
- **Message**：记忆总结降级时从 `message` 表读取原始对话（→ [rule-model.md R-007](rule-model.md#r-007记忆总结数据来源降级)）

---

*文档版本：v1.1*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — 补充 retry_count/tags/importance_score 字段、重试上限流转、交叉引用、analyze 修复（user_id 改 BIGINT、embedding 固定 2048）*
