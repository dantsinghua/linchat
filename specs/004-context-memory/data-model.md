# 数据模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md FR-008](spec.md#fr-008记忆-crud) | [spec.md FR-010](spec.md#fr-010向量检索) | [spec.md FR-011](spec.md#fr-011两表数据同步) | [rule-model.md R-005/R-006](rule-model.md#r-005embedding-状态流转) | [process-model.md §3/§5](process-model.md#3-记忆-embedding-异步处理流程) | [behavior-model.md §4](behavior-model.md#4-记忆-crud)

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
| type | VARCHAR(20) | NOT NULL, DEFAULT 'memory' | 类型：`memory` / `compaction` / `daily-summary` / `monthly-summary`（→ [rule-model.md R-008](rule-model.md#r-008记忆类型约束)）。用户通过 REST API 创建固定为 `memory`，其他类型仅系统内部创建 | **必填** |
| name | VARCHAR(200) | NULL | 名称，如 `daily-2026-01-29` | 选填 |
| content | TEXT | NOT NULL, MAX 10,000 字符 | 原始记忆文本。超出 10,000 字符由序列化器拒绝；Embedding 生成时若 token 数超出模型输入限制，截取前 N tokens 生成 embedding（→ [spec.md FR-008](spec.md#fr-008记忆-crud)） | **必填** |
| embedding_status | VARCHAR(20) | DEFAULT 'pending' | 状态：`pending` / `processing` / `done` / `failed`（→ [rule-model.md R-005](rule-model.md#r-005embedding-状态流转)） | 系统 |
| retry_count | INTEGER | DEFAULT 0 | embedding 生成重试计数，上限 3 次（→ [rule-model.md R-013](rule-model.md#r-013embedding-重试上限)） | 系统 |
| tags | JSONB | NULL | 预留字段，cronMem 流程可写入标签 | 选填 |
| importance_score | FLOAT | NULL | 预留字段，暂不使用 | 选填 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 | 自动 |
| updated_at | TIMESTAMP | NOT NULL, auto_now | 更新时间 | 自动 |

### 索引

| 索引名 | 字段 | 用途 |
|--------|------|------|
| idx_user_memory_user_id | user_id | 按用户查询 |
| idx_user_memory_embedding_status | embedding_status | 扫描待处理记录 |
| idx_user_memory_type | (user_id, type) | 按用户+类型查询（活跃用户判定、记忆总结降级） |
| idx_user_memory_retry | (embedding_status, retry_count) | 扫描可重试记录 |
| idx_user_memory_created | (user_id, created_at) | 按用户+时间范围查询（每日/每月总结数据采集） |
| idx_user_memory_content_tsv | content（GIN 索引，tsvector） | 全文检索（pg_jieba 中文分词），embedding 降级时的关键词匹配 |

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
| embedding | VECTOR(2048) | NULL | pgvector 向量，维度固定 2048，写入时校验非 2048 报错（→ [rule-model.md R-011](rule-model.md#r-011embedding-模型配置)） | 系统 |
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
                         → 永久失败(retry_count >= 3) → 保持 failed，退化为关键词匹配（tsvector + GIN + pg_jieba）
```

特殊场景：若 `model` 表中无 `type='embedding'` 的配置记录，抛出 `EmbeddingConfigNotFoundError` 异常，embedding_status 直接标记为 `failed`（→ [spec.md FR-010](spec.md#fr-010向量检索)）

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
- **ModelConfig**：
  - ContextService 从 `model` 表读取 `max_context_window` 计算有效窗口（→ [rule-model.md R-001](rule-model.md#r-001有效上下文窗口计算)）
  - EmbeddingService 从 `model` 表读取 `type='embedding'` 配置获取 API 地址和 API Key（→ [rule-model.md R-011](rule-model.md#r-011embedding-模型配置)）
- **Message**：记忆总结降级时从 `message` 表读取原始对话（→ [rule-model.md R-007](rule-model.md#r-007记忆总结数据来源降级)）

---

## 7. 上下文分层结构（逻辑模型，非持久化）

> 交叉引用：[spec.md FR-001](spec.md#fr-001分层上下文组装结构) | [spec.md FR-014](spec.md#fr-014动态-prompt-模板系统promptbuilder) | [behavior-model.md §1](behavior-model.md#1-分层上下文组装)

上下文组装为内存中的消息列表，不持久化存储，但其分层结构是系统核心数据模型：

| 层级 | 内容 | 固定/动态 | 预估 Token 数 | 消息格式 |
|------|------|-----------|---------------|----------|
| 1 | systemPrompt：基础角色 + 行为规范 + 功能模块 | 固定 | ~2k | `[system]` |
| 2.a | prompt 模板固定部分 | 固定 | ~1k | `[system]` |
| 2.b | 记忆内容（从记忆系统召回） | 动态 | 0 ~ 不固定 | `[system]` |
| 2.c | 工具内容（工具定义 + 调用结果） | 动态 | 0 ~ 不固定 | `[system]` |
| 2.d | 前对话（历史对话记录） | 动态 | 0 ~ 不固定 | `[user/assistant]` |
| 2.e | 用户当前输入 | 动态 | 0 ~ 4k（前端限制） | `[user]` |

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*更新日期：2026-01-31 — v2.1 新增 content_tsv GIN 索引（pg_jieba 全文检索），embedding 降级关键词匹配明确为 tsvector + pg_jieba*
