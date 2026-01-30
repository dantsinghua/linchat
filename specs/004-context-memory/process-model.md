# 流程模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [behavior-model.md](behavior-model.md)

---

## 1. 对话处理主流程（含上下文管理与记忆召回）

> 交叉引用：[behavior-model.md §5](behavior-model.md#5-记忆自动召回) | [rule-model.md R-001/R-002](rule-model.md#r-001有效上下文窗口计算)

```
用户发送消息
    │
    ▼
┌─────────────────────────┐
│  1. Memory Retrieval    │  ← 基于 user_id 隔离查询（→ R-004）
│  语义搜索相关记忆        │     pgvector 向量检索（→ FR-006）
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  2. Context Assembly    │  ← system prompt + 召回记忆 + 历史消息 + 用户输入
│  组装上下文              │     召回记忆注入位置：system prompt 之后（→ FR-002）
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  3. Context Management  │  ← 动态窗口 = model.max_context_window * 0.9（→ R-001）
│  上下文窗口管理          │     Token 计数：tiktoken cl100k_base（→ R-017）
│                         │
│  3a. 计算 token 总量     │
│  3b. 超限？→ 渐进式裁剪  │     （→ R-002）
│  3c. 仍超限？→ 压缩      │     （→ R-003, R-014）
│      → 存入记忆表        │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  4. LLM Call            │  ← 从 model 表获取配置
│  流式调用 LLM           │     Langfuse 追踪（→ R-016）
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  5. Response Output     │  ← SSE 流式响应
│  输出响应                │
└─────────────────────────┘
```

---

## 2. 上下文裁剪与压缩流程

> 交叉引用：[behavior-model.md §2/§3](behavior-model.md#2-渐进式上下文裁剪) | [rule-model.md R-002/R-003/R-014](rule-model.md#r-002裁剪保留规则)

```
计算 token 总量（tiktoken cl100k_base）
    │
    ▼
超出有效窗口？ ─── 否 ──→ 直接使用
    │
    是
    ▼
┌─────────────────────────┐
│  渐进式裁剪              │
│  1. 保留 system 消息     │
│  2. 保留最近 N 轮        │
│  3. 保留召回记忆         │
│  4. 从最早开始丢弃       │
└────────┬────────────────┘
         │
         ▼
仍然超限？ ─── 否 ──→ 使用裁剪后消息
    │
    是
    ▼
┌─────────────────────────────────────┐
│  Safeguard 压缩                      │
│  0. 获取 Redis 分布式锁              │
│     key=compress:{user_id}           │
│     未获锁 → 等待锁释放后重新检查     │
│  1. 取全部被裁剪消息                  │
│  2. LLM 生成摘要                     │
│     失败 → 重试 3 次                 │
│     3 次全失败 → 回退简单截断（→ R-014）│
│  3. 摘要替换原始消息                  │
│  4. create_memory(type='compaction') │
│     写入记忆表                       │
│  5. 重复直到 < effective_window      │
│  6. 释放 Redis 锁                    │
└────────┬────────────────────────────┘
         │
         ▼
    使用压缩后消息
```

---

## 3. 记忆 Embedding 异步处理流程

> 交叉引用：[behavior-model.md §7](behavior-model.md#7-embedding-异步生成含重试) | [rule-model.md R-013/R-015](rule-model.md#r-013embedding-重试上限) | [data-model.md §4](data-model.md#4-embedding_status-状态流转)

```
记忆创建/更新
    │
    ▼
写入 user_memory
(embedding_status = 'pending', retry_count = 0)
    │
    ▼
投递异步任务 ──────────────────────┐
    │                              │
    ▼                              ▼
返回 API 响应              ┌──────────────────────────┐
（不阻塞用户操作）          │  Celery Worker            │
                           │  1. status → processing   │
                           │  2. 调用 Embedding API     │
                           │  3. 分块 + 生成向量        │
                           │  4. 写入 user_memory_embedding │
                           │  5. 成功 → status = done   │
                           │     失败 → retry_count += 1│
                           │       < 3 → status = failed│
                           │       >= 3 → 永久 failed   │
                           │       （退化为关键词匹配）  │
                           └──────────────────────────┘

定时任务（间隔 5 分钟）
    │
    ▼
扫描 embedding_status='failed' 且 retry_count < 3
  + embedding_status='pending' 超时记录
    │
    ▼
重新投递异步任务
```

---

## 4. 记忆总结定时任务流程

> 交叉引用：[behavior-model.md §6](behavior-model.md#6-核心总结方法) | [rule-model.md R-007/R-012](rule-model.md#r-007记忆总结数据来源降级)

```
每日 00:00 / 每月 1 日 00:00（→ R-012）
    │
    ▼
遍历所有活跃用户
    │
    ▼ (每个用户)
┌─────────────────────────────┐
│  查找数据来源                │
│  1. 查 user_memory 表       │
│     每日：type='compaction'  │
│     每月：type='daily-summary' │
│  2. 无数据？→ 降级到 message 表 │  （→ R-007）
│  3. 仍无数据？→ 跳过          │
└────────┬────────────────────┘
         │
         ▼ (有数据)
┌─────────────────────────────┐
│  调用 summarize_and_store   │  （→ behavior-model §6）
│  1. LLM 生成摘要            │     Langfuse 追踪（→ R-016）
│  2. 写入 user_memory        │
│     每日：type='daily-summary' │
│     每月：type='monthly-summary' │
│  3. 异步生成 embedding       │
└─────────────────────────────┘
```

---

## 5. 两表数据同步时序

> 交叉引用：[data-model.md §5](data-model.md#5-数据同步规则) | [rule-model.md R-006](rule-model.md#r-006两表一致性) | [behavior-model.md §4](behavior-model.md#4-记忆-crud)

```
             user_memory               user_memory_embedding
创建  ──→  INSERT (status=pending,
           retry_count=0)
                    │
                    ▼ (异步)
                                       INSERT (embedding 数据)
           UPDATE (status=done)

更新  ──→  UPDATE content
           UPDATE (status=pending,
           retry_count=0)
                    │
                    ▼ (异步)
                                       INSERT (新 embedding)
                                       DELETE (旧 embedding)
           UPDATE (status=done)

删除  ──→  DELETE
                                       CASCADE DELETE (自动)
```

---

*文档版本：v1.1*
*创建日期：2026-01-29*
*更新日期：2026-01-30 — 补充 Redis 锁/LLM 失败回退/重试上限/retry_count/可观测性标注、全文交叉引用、analyze 修复（conversation_id→user_id/max_tokens→effective_window/压缩调create_memory）*
