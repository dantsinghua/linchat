# 流程模型 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-29

> 交叉引用：[spec.md](spec.md) | [data-model.md](data-model.md) | [rule-model.md](rule-model.md) | [behavior-model.md](behavior-model.md)

---

## 1. LangGraph 四流程编排总览

> 交叉引用：[spec.md FR-007](spec.md#fr-007langgraph-流程编排) | [rule-model.md R-018](rule-model.md#r-018langgraph-流程工具集隔离) | [behavior-model.md §3](behavior-model.md#3-langgraph-流程编排)

```
用户发送消息
    │
    ▼
┌─────────────────────────────────────┐
│  PromptBuilder 分层组装              │  ← behavior-model §1
│  1(systemPrompt) + 2.a(模板)        │
│  + 2.b(记忆) + 2.c(工具)            │
│  + 2.d(前对话) + 2.e(用户输入)       │
│  全部加载后计算 token 总量            │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  上下文超限检查                       │  ← R-001, R-002
│  总 token > 有效窗口(max * 0.9)?     │
│                                      │
│  否 → 直接进入 chat 流程              │
│  是 → 进入优先级压缩（§2）            │
└────────┬───────────────┬────────────┘
         │(未超限)       │(超限)
         │               ▼
         │    ┌──────────────────────┐
         │    │ 优先级压缩流程（§2）  │
         │    │ SSE: context_compacting│
         │    │ d → c → b 依次处理   │
         │    │ SSE: context_compacted │
         │    └──────────┬───────────┘
         │               │
         ▼               ▼
┌─────────────────────────────────────┐
│  chat 流程                           │  ← LangGraph StateGraph
│  输入：完整上下文（1 + 2.a~2.e）     │
│  工具集：记忆工具（本期仅此）         │
│         + python repl（后续特性预留） │
│         + bravo search（后续特性预留）│
│         + home assistant（后续预留）  │
│  流程：Agent → Tool → End            │
│  输出：SSE 流式响应                   │
│  可观测性：Langfuse 追踪（→ R-016）  │
└─────────────────────────────────────┘
```

### 流程调用关系

```
串行前置模式：

  上下文超限？
    ├── 否 → chat 流程
    └── 是 → context 流程(处理 2.d)
              ├── 仍超限 → context 流程(处理 2.c)
              │             ├── 仍超限 → memory 流程(处理 2.b)
              │             │             └── 仍超限 → 直接截断
              │             └── 未超限 → chat 流程
              └── 未超限 → chat 流程

  cronMem 流程（独立，定时触发，不在对话链路中）
```

---

## 2. 优先级驱动的上下文压缩流程

> 交叉引用：[spec.md FR-003](spec.md#fr-003优先级驱动的上下文压缩) | [spec.md FR-004](spec.md#fr-004上下文工具集仅上下文处理流程使用) | [behavior-model.md §2](behavior-model.md#2-优先级驱动的上下文压缩) | [rule-model.md R-003/R-014/R-019](rule-model.md#r-003优先级驱动的上下文压缩)

```
总 token 数 > 有效窗口
    │
    ▼
┌──────────────────────────────────────────────┐
│  0. 获取 Redis 分布式锁                       │
│     key=compress:{user_id}                    │
│     未获锁 → 等待锁释放后重新检查 token         │
│  1. 发送 SSE context_compacting 事件          │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  第一步：压缩前对话(2.d)                       │
│  启动 context 流程（LangGraph）                │
│  输入：(1) + 2.a + 2.e + 2.d                  │
│  工具：contextCompact / contextExtract /       │
│        contextPrune                            │
│  超长输入：直接截断                             │
└────────┬─────────────────────────────────────┘
         │
         ▼
仍然超限？ ─── 否 ──→ 跳到步骤 5
    │
    是
    ▼
┌──────────────────────────────────────────────┐
│  第二步：压缩工具内容(2.c)                     │
│  启动 context 流程（LangGraph）                │
│  输入：(1) + 2.a + 2.e + 2.c                  │
│  工具：同上                                    │
│  超长输入：直接截断                             │
└────────┬─────────────────────────────────────┘
         │
         ▼
仍然超限？ ─── 否 ──→ 跳到步骤 5
    │
    是
    ▼
┌──────────────────────────────────────────────┐
│  第三步：处理记忆内容(2.b)                     │
│  启动 memory 流程（LangGraph）                 │
│  输入：(1) + 2.a + 2.e + 2.b                  │
│  工具：memSearch / memCache / memUpdate /       │
│        memDelete                               │
│  超长输入：直接截断                             │
└────────┬─────────────────────────────────────┘
         │
         ▼
仍然超限？ ─── 否 ──→ 跳到步骤 5
    │
    是
    ▼
┌──────────────────────────────────────────────┐
│  第四步：最终截断                               │
│  对 d + c + b 处理后的结果                      │
│  统一再处理并直接截断至有效窗口大小              │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  5. 成功压缩时：                               │
│     create_memory(type='compaction')           │
│     写入记忆表                                 │
│  6. 发送 SSE context_compacted 事件            │
│  7. 释放 Redis 锁                              │
└──────────────────────────────────────────────┘

LLM 调用失败处理（→ R-014）：
  重试 3 次 → 全失败 → 回退简单截断（丢弃最早消息）
  回退截断不生成 compaction 记忆
  保证对话流程不中断

安全兜底（→ R-019）：
  有效窗口 = max * 0.9，10% buffer 容纳中间过程超限
  超过 100% → 直接截断，不报错终止
```

---

## 3. 记忆 Embedding 异步处理流程

> 交叉引用：[behavior-model.md §8](behavior-model.md#8-embedding-异步生成含重试) | [rule-model.md R-011/R-013/R-015](rule-model.md#r-013embedding-重试上限) | [data-model.md §4](data-model.md#4-embedding_status-状态流转)

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
                           │  2. 从 model 表获取        │
                           │     type='embedding' 配置  │
                           │     无配置 → EmbeddingConfig│
                           │     NotFoundError → failed │
                           │  3. 调用 Embedding API     │
                           │  4. 校验维度 = 2048        │
                           │  5. content 超长 →          │
                           │     截取前 N tokens        │
                           │  6. 写入 user_memory_embedding │
                           │  7. 成功 → status = done   │
                           │     失败 → retry_count += 1│
                           │       < 3 → status = failed│
                           │       >= 3 → 永久 failed   │
                           │       （退化为关键词匹配    │
                           │        tsvector+GIN+pg_jieba）│
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

> 交叉引用：[spec.md FR-012](spec.md#fr-012记忆总结) | [behavior-model.md §7](behavior-model.md#7-核心总结方法) | [rule-model.md R-007/R-012/R-022](rule-model.md#r-007记忆总结数据来源降级)

```
每日 00:00 / 每月 1 日 00:00（→ R-012）
    │
    ▼
查找活跃用户（→ R-007）
  每日：当天有新 compaction 记忆或新 message 记录的用户
  每月：当月有 daily-summary 记忆或新 message 记录的用户
    │
    ▼ (每个活跃用户)
┌─────────────────────────────────┐
│  查找数据来源                    │
│  1. 查 user_memory 表           │
│     每日：type='compaction'     │
│     每月：type='daily-summary'  │
│  2. 无数据？→ 降级到 message 表  │  （→ R-007）
│  3. 仍无数据？→ 跳过            │
└────────┬────────────────────────┘
         │
         ▼ (有数据)
┌─────────────────────────────────┐
│  启动 cronMem 流程               │  （→ behavior-model §3）
│  输入：专用 system prompt        │
│       （参考 mem0 prompt 设计）  │
│       + 记忆/对话内容            │
│  工具：无（仅 Agent → End）      │
│  输出：content、tags、date 等    │
│                                  │
│  LLM 调用失败（→ R-022）：       │
│    重试 3 次后跳过该用户         │
│    记录 WARNING 日志             │
│    下次定时任务时重新尝试        │
└────────┬────────────────────────┘
         │
         ▼ (成功)
┌─────────────────────────────────┐
│  调用 create_memory 存储         │
│  每日：type='daily-summary'     │
│       name='daily-2026-01-29'   │
│  每月：type='monthly-summary'   │
│       name='monthly-2026-01'    │
│  异步生成 embedding              │
│  可观测性：Langfuse 追踪         │
│           （→ R-016）            │
└─────────────────────────────────┘
```

---

## 5. 两表数据同步时序

> 交叉引用：[data-model.md §5](data-model.md#5-数据同步规则) | [rule-model.md R-006](rule-model.md#r-006两表一致性) | [behavior-model.md §5](behavior-model.md#5-记忆-crud)

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

## 6. 前端 SSE 压缩状态推送流程

> 交叉引用：[spec.md FR-016](spec.md#fr-016前端上下文压缩状态提示) | [rule-model.md R-020](rule-model.md#r-020前端-sse-压缩状态事件)

```
上下文压缩触发
    │
    ▼
后端发送 SSE 事件：context_compacting
    │
    ▼
前端对话框左下角显示"正在压缩上下文"状态标识
    │
    ▼
... 压缩处理中（context/memory 流程执行）...
    │
    ▼
后端发送 SSE 事件：context_compacted
    │
    ▼
前端移除"正在压缩上下文"状态标识
```

- 复用现有对话 SSE 流，不开设独立通道
- 事件类型：`context_compacting`（开始） / `context_compacted`（完成）
- 用户切换会话后返回，若压缩仍在进行则继续显示提示

---

*文档版本：v2.0*
*创建日期：2026-01-29*
*更新日期：2026-01-31 — v2.1 embedding 永久失败降级明确为 tsvector + GIN + pg_jieba 关键词匹配*
