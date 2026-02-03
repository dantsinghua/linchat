# 实施计划：上下文与记忆管理 (M1b)

**Branch**: `004-context-memory` | **Date**: 2026-01-31 | **Spec**: [spec.md](spec.md)
**Input**: specs/004-context-memory/spec.md + data-model.md + process-model.md + behavior-model.md + rule-model.md

## Summary

构建分层上下文窗口管理和数据库化的长期记忆系统。核心能力包括：五段式上下文组装（systemPrompt + 模板 + 记忆 + 工具 + 前对话 + 用户输入）、优先级驱动的压缩策略（d → c → b）、记忆 CRUD + pgvector 向量检索 + pg_jieba 关键词混合检索、LangGraph 四流程编排（chat/context/memory/cronMem）、Celery 异步任务（embedding 生成 + 定时总结）。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, LangGraph, LangChain, tiktoken, pgvector, openai SDK, Celery 5.3+, Langfuse
**Storage**: PostgreSQL 15 + pgvector + pg_jieba (主存储), Redis (缓存/分布式锁/Celery Broker DB2)
**Testing**: pytest + pytest-django + pytest-asyncio + pytest-cov
**Target Platform**: Linux server (单一生产环境)
**Project Type**: Web application (Django 后端 + Next.js 前端)
**Performance Goals**: 语义搜索 < 500ms, 上下文裁剪额外延迟 < 500ms, 大模型首令牌 < 2s
**Constraints**: 有效窗口 = max_context_window × 0.9, 向量维度固定 2048, 前端输入限制 4k tokens, 模型最小上下文 ≥ 10,000 tokens
**Scale/Scope**: 单租户多用户，所有隔离按 user_id 粒度

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 合规状态 |
|------|------|---------|
| 1.1 关注点分离 | View → Service → Repository 分层 | ✅ 新建 `apps/memory/` 独立 app，ContextService 放 `apps/chat/services/` |
| 1.2 接口设计 | REST API + SSE 流式 | ✅ 记忆 CRUD REST API + 复用现有 SSE 流推送压缩状态 |
| 1.3 数据一致性 | PostgreSQL 为主，写操作原子性 | ✅ 两表通过事务 + FK CASCADE + 异步最终一致性保证 |
| 2.1 Python 规范 | PEP 8 + Black + isort + 类型注解 | ✅ 所有新代码遵循 |
| 2.2 前端规范 | ESLint + Prettier + TypeScript strict | ✅ SSE 事件处理新增类型定义 |
| 3.1 测试覆盖 | 总体 80%+, 服务层 95%+ | ✅ 全量测试计划 |
| 4.1 安全 | user_id 隔离, httpOnly Cookie | ✅ 所有查询强制 user_id, API Key SM4 加密 |
| 4.3 LLM 异常处理 | 统一异常类型 + 重试策略 | ✅ 压缩/总结 LLM 调用复用异常处理框架 |
| 5.1 性能 | p95 响应时间 | ✅ 语义搜索 < 500ms, 裁剪延迟 < 500ms |

**宪法违规项**：无

## Project Structure

### Documentation (this feature)

```text
specs/004-context-memory/
├── plan.md                    # 本文件（实施计划）
├── research.md                # 技术研究决策
├── mem0-prompt-reference.md   # mem0 Prompt 参考设计
├── spec.md                    # 功能规范
├── data-model.md              # 数据模型
├── process-model.md           # 流程模型
├── behavior-model.md          # 行为模型
├── rule-model.md              # 规则模型
├── checklists/
│   └── requirements.md        # 需求检查清单
└── tasks.md                   # 任务清单（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── memory/                         # 【新建】记忆管理模块
│   │   ├── __init__.py
│   │   ├── models.py                   # UserMemory, UserMemoryEmbedding
│   │   ├── serializers.py              # 记忆 CRUD 序列化器
│   │   ├── views.py                    # 记忆 REST API 视图
│   │   ├── services.py                 # MemoryService（CRUD、搜索、总结）
│   │   ├── repositories.py            # MemoryRepository, EmbeddingRepository
│   │   ├── tasks.py                    # Celery 任务（embedding 生成、定时总结、重试扫描）
│   │   ├── tools.py                    # LangGraph 记忆工具（memSearch/memCache/memUpdate/memDelete）
│   │   └── urls.py                     # 记忆 API 路由
│   ├── chat/
│   │   ├── services/
│   │   │   ├── context_service.py      # 【新建】ContextService（窗口计算、压缩编排）
│   │   │   ├── chat_service.py         # 【修改】集成上下文管理流程
│   │   │   └── agent_service.py        # 【修改】集成 LangGraph 四流程
│   │   ├── prompts.py                  # 【新建】PromptBuilder 动态 prompt 模板系统（分层组装、模块注册）
│   │   ├── agent.py                    # 【修改】新增 context/memory/cronMem 流程工厂
│   │   ├── tools.py                    # 【新建】上下文工具（contextCompact/contextExtract/contextPrune）
│   │   └── sse.py                      # 【修改】新增 context_compacting/context_compacted 事件
│   ├── common/
│   │   └── tokenizer.py               # 【新建】tiktoken 封装（cl100k_base 编码）
│   └── models/
│       └── models.py                   # 【不改】ModelConfig 已支持 type=embedding
├── core/
│   ├── celery.py                       # 【新建】Celery 应用配置
│   └── settings.py                     # 【修改】新增 Celery、apps.memory 配置
└── tests/
    └── memory/                         # 【新建】记忆模块测试
        ├── test_models.py
        ├── test_services.py
        ├── test_repositories.py
        ├── test_views.py
        ├── test_tasks.py
        ├── test_tools.py
        └── test_isolation.py           # 用户隔离专项测试

frontend/
└── src/
    ├── types/
    │   └── index.ts                    # 【修改】新增 context_compacting/context_compacted 事件类型
    ├── hooks/
    │   └── useChatStream.ts            # 【修改】处理压缩状态 SSE 事件
    ├── stores/
    │   └── chatStore.ts                # 【修改】新增 isCompacting 状态
    └── components/chat/
        └── MessageList.tsx             # 【修改】显示"正在压缩上下文"状态提示
```

**Structure Decision**: Web application 模式。记忆管理作为独立 `apps/memory/` 模块，上下文管理作为 `apps/chat/services/context_service.py` 新增服务（编排层），两者通过服务层接口松耦合。前端仅新增 SSE 事件处理和状态提示 UI。

---

## Phase 0: 技术研究

详见 [research.md](research.md)，所有技术决策已确定：

| 研究项 | 决策 | 状态 |
|--------|------|------|
| RES-001 pgvector 集成 | `pgvector` PyPI 包 + Django ORM VectorField | ✅ 已确定 |
| RES-002 tiktoken 集成 | tiktoken cl100k_base + 全局缓存编码器 | ✅ 已确定 |
| RES-003 Celery 异步任务 | Celery 5.3+ + Redis DB2 Broker + Beat | ✅ 已确定 |
| RES-004 Embedding API | openai SDK + AsyncOpenAI + SM4 解密 | ✅ 已确定 |
| RES-005 Redis 分布式锁 | redis-py Lock (SET NX EX) | ✅ 已确定 |
| RES-006 Django App 结构 | 新建 `apps/memory/` 独立 app | ✅ 已确定 |
| RES-007 向量维度 | 固定 2048，VectorField(dimensions=2048) | ✅ 已确定 |
| RES-008 关键词降级 | PostgreSQL tsvector + GIN + pg_jieba 中文分词 | ✅ 已确定 |
| RES-009~011 mem0 Prompt | 已抽取为 mem0-prompt-reference.md | ✅ 已确定 |

---

## Phase 1: 数据模型与合约设计

详见 [data-model.md](data-model.md)，核心产出：

### 新增数据库表

| 表名 | 字段数 | 说明 |
|------|--------|------|
| `user_memory` | 10 字段 + 6 索引 | 记忆元数据（主表） |
| `user_memory_embedding` | 8 字段 + 2 索引 | 记忆向量（从表，FK CASCADE） |

### 数据库扩展

| 扩展 | 用途 |
|------|------|
| pgvector | VECTOR(2048) 类型 + 向量距离函数 |
| pg_jieba | 中文分词（全文检索 tsvector GIN 索引） |

### API 合约

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/memories/` | GET | 列出当前用户记忆（分页） |
| `/api/v1/memories/` | POST | 创建记忆（type 固定 memory） |
| `/api/v1/memories/{id}/` | GET | 获取单条记忆详情 |
| `/api/v1/memories/{id}/` | PUT | 更新记忆内容 |
| `/api/v1/memories/{id}/` | DELETE | 删除记忆（级联删除 embedding） |
| `/api/v1/memories/search/` | POST | 语义搜索（混合检索，TopK=5） |

### SSE 事件扩展

| 事件类型 | 触发时机 | 前端行为 |
|----------|----------|----------|
| `context_compacting` | 压缩开始 | 对话框左下角显示"正在压缩上下文" |
| `context_compacted` | 压缩完成 | 移除状态提示 |

---

## Phase 2: 实施阶段

### 阶段 A — 基础设施搭建（P0，前置依赖）

| 任务 | 说明 | 依赖 |
|------|------|------|
| A-01 | Docker PostgreSQL 安装 pgvector + pg_jieba 扩展 | 无 |
| A-02 | 添加 Python 依赖：tiktoken, pgvector, celery, django-celery-beat | 无 |
| A-03 | 创建 `apps/common/tokenizer.py` — tiktoken 封装 | A-02 |
| A-04 | 创建 `core/celery.py` — Celery 应用配置 + settings.py 更新 | A-02 |
| A-05 | 创建 `apps/memory/` 骨架（models, services, repos, views, urls, serializers, tasks, tools） | A-01, A-02 |
| A-06 | Django migration：创建 user_memory + user_memory_embedding 表（含索引） | A-01, A-05 |

### 阶段 B — 记忆 CRUD + Embedding（P0）

| 任务 | 说明 | 依赖 |
|------|------|------|
| B-01 | `MemoryRepository` + `EmbeddingRepository` — ORM 数据访问层 | A-06 |
| B-02 | `MemoryService` — CRUD 方法（create/update/delete/get/list） | B-01 |
| B-03 | `MemorySerializer` — 请求校验（content ≤ 10,000 字符，type 不可客户端指定） | B-01 |
| B-04 | 记忆 REST API 视图（views.py + urls.py） | B-02, B-03 |
| B-05 | Celery 异步任务：`generate_embedding` — Embedding 生成（含状态流转、重试、维度校验） | A-04, B-01 |
| B-06 | Celery 定时任务：`retry_failed_embeddings` — 每 5 分钟扫描重试 | B-05 |
| B-07 | 用户隔离测试（test_isolation.py）— 跨用户不可见 | B-04 |

### 阶段 C — 语义搜索与混合检索（P1）

| 任务 | 说明 | 依赖 |
|------|------|------|
| C-01 | `EmbeddingRepository.vector_search()` — pgvector 向量检索 | B-01 |
| C-02 | `MemoryRepository.keyword_search()` — tsvector + GIN + pg_jieba 全文检索 | B-01 |
| C-03 | `MemoryService.search_memory()` — 混合检索（向量 0.7 + 关键词 0.3 加权排序） | C-01, C-02 |
| C-04 | `MemoryService.retrieve_relevant_memories()` — 对话前自动召回 | C-03 |
| C-05 | 搜索 API 端点（POST /api/v1/memories/search/） | C-03 |

### 阶段 D — PromptBuilder + 上下文管理（P0）

| 任务 | 说明 | 依赖 |
|------|------|------|
| D-01 | 完善 `prompts.py` — PromptBuilder 全量实现（6 个 build 方法 + 模块注册） | A-03, C-04 |
| D-02 | 创建 `context_service.py` — ContextService（有效窗口计算、超限检查、Token 统计） | A-03, D-01 |
| D-03 | 创建 `chat/tools.py` — 上下文工具（contextCompact/contextExtract/contextPrune） | D-02 |
| D-04 | 创建 `memory/tools.py` — 记忆工具（memSearch/memCache/memUpdate/memDelete） | B-02, C-03 |
| D-05 | Redis 分布式锁集成 — compress:{user_id}（锁工具在此实现，E-04 压缩编排中调用） | D-02 |
| D-06 | 创建专用 Prompt 模板（COMPACTION/DAILY/MONTHLY/CRONMEM） | D-01 |

### 阶段 E — LangGraph 四流程编排（P0）

| 任务 | 说明 | 依赖 |
|------|------|------|
| E-01 | `agent.py` 新增 context 流程工厂 — StateGraph + 上下文工具集 | D-03 |
| E-02 | `agent.py` 新增 memory 流程工厂 — StateGraph + 记忆工具集 | D-04 |
| E-03 | `agent.py` 新增 cronMem 流程工厂 — StateGraph 无工具（Agent → End） | D-06 |
| E-04 | `context_service.py` 扩展 — 优先级压缩编排（d → c → b → 截断） | E-01, E-02, D-05 |
| E-05 | `agent_service.py` 修改 — 集成上下文超限检查 + 压缩前置流程 | E-04 |
| E-06 | SSE 事件扩展 — context_compacting / context_compacted | E-04 |

### 阶段 F — 记忆总结 + 前端（P1/P2）

| 任务 | 说明 | 依赖 |
|------|------|------|
| F-01 | `MemoryService.summarize_and_store()` — 核心总结方法 | E-03, B-02 |
| F-02 | Celery 定时任务：`generate_daily_summary` — 每日 00:00 | F-01 |
| F-03 | Celery 定时任务：`generate_monthly_summary` — 每月 1 日 00:00 | F-01 |
| F-04 | 前端：types 新增 SSE 事件类型 + chatStore 新增 isCompacting 状态 | E-06 |
| F-05 | 前端：useChatStream 处理压缩事件 + MessageList 显示状态提示 | F-04 |

### 阶段 G — 全量测试 + 可观测性

| 任务 | 说明 | 依赖 |
|------|------|------|
| G-01 | 单元测试：MemoryService（CRUD + 搜索 + 总结）| F-03 |
| G-02 | 单元测试：ContextService（窗口计算 + 压缩编排）| E-05 |
| G-03 | 单元测试：PromptBuilder（分层组装 + Token 计数）| D-01 |
| G-04 | 集成测试：LangGraph 四流程工具集隔离 | E-05 |
| G-05 | 集成测试：Embedding 生成 + 重试 + 降级关键词匹配 | B-06 |
| G-06 | 集成测试：定时总结任务（每日/每月 + 降级策略）| F-03 |
| G-07 | Langfuse 追踪集成：LLM 压缩 + 总结 + Embedding 调用 | E-05, F-03 |
| G-08 | Django logging 配置：关键事件日志（WARNING/INFO）| G-07 |

---

## 依赖关系图

```
A（基础设施）
├─→ B（记忆 CRUD + Embedding）
│   ├─→ C（语义搜索）
│   │   └─→ D（PromptBuilder + 上下文管理）
│   │       ├─→ E（LangGraph 四流程）
│   │       │   └─→ F（记忆总结 + 前端）
│   │       │       └─→ G（全量测试）
│   │       └─→ E
│   └─→ D
└─→ A
```

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| pgvector 扩展安装失败 | 低 | 高 | Docker 构建时预装，CI 验证 |
| pg_jieba 安装复杂 | 中 | 中 | 提供 Dockerfile + 安装脚本，备选 zhparser |
| Embedding 服务不稳定 | 中 | 低 | 降级为关键词匹配，不阻塞用户操作 |
| 压缩 LLM 调用超时 | 中 | 中 | 3 次重试 + 回退简单截断，保证对话不中断 |
| Celery Worker 宕机 | 低 | 中 | 定时扫描 pending/failed 记录自动恢复 |
| 并发压缩竞争 | 低 | 低 | Redis 分布式锁 + 锁释放后重新检查 |

---

## Complexity Tracking

> 无宪法违规需要辩护。

| 复杂度因素 | 说明 | 必要性 |
|-----------|------|--------|
| 新建 `apps/memory/` app | 记忆管理是独立业务域，有自己的数据模型和服务 | 符合宪法 1.1 关注点分离 |
| 四个 LangGraph 流程 | 规范明确要求（spec.md FR-007），各流程职责边界清晰 | 规范强制 |
| 两表数据同步 | 主从表 + 异步最终一致性 | 向量检索的技术需求，无法简化 |
| Celery 引入 | 宪法技术栈已列出，异步 embedding + 定时任务必需 | 宪法合规 |

---

*文档版本：v3.0*
*创建日期：2026-01-29*
*更新日期：2026-01-31 — v3.0 基于最新五个模型文档（spec/data/process/behavior/rule）全面重写，整合实际代码库现状*
