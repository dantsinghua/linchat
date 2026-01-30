# Implementation Plan: 上下文与记忆管理 (M1b)

**Branch**: `004-context-memory` | **Date**: 2026-01-30 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-context-memory/spec.md`

## Summary

构建动态上下文窗口管理和数据库化长期记忆系统。核心能力包括：基于模型配置的上下文窗口动态计算与渐进式裁剪、Safeguard 压缩（LLM 生成摘要）、记忆 CRUD（含异步 Embedding 生成）、pgvector 语义搜索与关键词匹配降级、以及定时记忆总结机制（每日/每月）。

本期仅实现后端 REST API，不含前端 UI。

## Technical Context

**Language/Version**: Python 3.11+ (后端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, Celery 5.3+, tiktoken, pgvector, openai SDK, Langfuse
**Storage**: PostgreSQL 15 + pgvector 扩展（主存储）, Redis（缓存/分布式锁/Celery Broker）
**Testing**: pytest + pytest-django + pytest-asyncio + pytest-cov
**Target Platform**: Linux server (ASGI mode)
**Project Type**: Web application (backend-only for this milestone)
**Performance Goals**: 语义搜索 < 500ms, 上下文管理不显著增加首 token 延迟
**Constraints**: 用户记忆严格隔离 (R-004), 写操作原子性, Embedding 最终一致性
**Scale/Scope**: 单租户部署, 中等数据量

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### 初始检查（Phase 0 前）

| 条款 | 要求 | 状态 | 说明 |
|------|------|------|------|
| 1.1 关注点分离 | 视图→服务→仓库分层 | ✅ PASS | 新建 `apps/memory/` 遵循分层架构 |
| 1.2 接口设计 | RESTful + 统一响应格式 | ✅ PASS | API 遵循 `/api/v1/` 基础路径，标准响应格式 |
| 1.3 数据一致性 | PostgreSQL 主存储，事务保护 | ✅ PASS | 元数据事务写入，Embedding 异步最终一致 |
| 2.1 Python 规范 | PEP8 + Black + isort + 类型注解 | ✅ PASS | 所有新代码遵循 |
| 3.1 测试覆盖 | 总体 80%+, 服务层 95% | ✅ PASS | 计划包含完整测试 |
| 4.1 认证授权 | Token + httpOnly Cookie | ✅ PASS | 复用现有认证中间件 |
| 4.2 数据保护 | API Key SM4 加密 | ✅ PASS | Embedding 模型 API Key 通过 SM4 解密 |
| 4.3 LLM 异常处理 | 统一异常类 + 重试策略 | ✅ PASS | 压缩/总结 LLM 调用遵循异常处理规范 |
| 5.1 性能指标 | 搜索 < 500ms | ✅ PASS | pgvector 索引 + 限制返回条数 |
| 8.2 ASGI 模式 | uvicorn 启动 | ✅ PASS | 不改变启动方式 |

### Phase 1 后复查

| 新增检查项 | 状态 | 说明 |
|-----------|------|------|
| 禁止原生 SQL | ✅ PASS | pgvector 通过 `pgvector.django` ORM 集成，向量操作使用 ORM 表达式 |
| Celery 异步任务 | ✅ PASS | 符合宪法技术栈要求（Celery 5.3+），使用 Redis Broker |
| 数据补偿机制 | ✅ PASS | 定时扫描 failed/pending 超时记录，自动重试 |

**GATE RESULT: ✅ ALL PASS — 无违规项**

## Project Structure

### Documentation (this feature)

```text
specs/004-context-memory/
├── plan.md              # 本文件
├── spec.md              # 功能规范
├── research.md          # Phase 0 技术研究
├── data-model.md        # 数据模型设计
├── rule-model.md        # 业务规则
├── behavior-model.md    # 行为模型
├── process-model.md     # 流程模型
├── quickstart.md        # 快速启动指南
├── contracts/           # API 合约
│   └── memory-api.yaml  # 记忆 CRUD + 搜索 OpenAPI 定义
├── tasks.md             # 任务清单（/speckit.tasks 生成）
└── checklists/
    └── requirements.md  # 验收标准
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── memory/                    # 新建：记忆管理模块
│   │   ├── __init__.py
│   │   ├── models.py              # UserMemory, UserMemoryEmbedding
│   │   ├── serializers.py         # 记忆 CRUD 序列化器
│   │   ├── views.py               # 记忆 API 视图（CRUD + 搜索）
│   │   ├── services.py            # MemoryService（CRUD、搜索、总结）
│   │   ├── repositories.py        # MemoryRepository, EmbeddingRepository
│   │   ├── tasks.py               # Celery 任务（embedding 生成、定时总结、重试扫描）
│   │   └── urls.py                # URL 路由
│   ├── chat/
│   │   ├── services.py            # 扩展：新增 ContextService 类
│   │   └── agent.py               # 修改：集成上下文管理和记忆召回
│   └── common/
│       └── tokenizer.py           # 新建：tiktoken 封装（token 计数工具）
├── core/
│   ├── celery.py                  # 新建：Celery 应用配置
│   ├── settings.py                # 修改：新增 Celery/pgvector/memory 配置
│   └── __init__.py                # 修改：导入 Celery app
└── tests/
    ├── memory/                    # 新建：记忆模块测试
    │   ├── __init__.py
    │   ├── test_models.py         # 模型测试
    │   ├── test_services.py       # 服务层测试（95% 覆盖）
    │   ├── test_repositories.py   # 仓库层测试
    │   ├── test_views.py          # API 视图测试
    │   ├── test_tasks.py          # Celery 任务测试
    │   └── test_isolation.py      # 用户隔离专项测试
    └── chat/
        └── test_context_service.py  # 上下文管理测试
```

**Structure Decision**: 新建 `apps/memory/` 模块作为独立业务域，上下文管理（ContextService）放在 `apps/chat/services.py` 中作为编排层，两者通过服务接口解耦。

## 模块职责划分

### apps/memory/ — 记忆管理

| 文件 | 职责 |
|------|------|
| `models.py` | `UserMemory`、`UserMemoryEmbedding` 数据模型定义 |
| `services.py` | `MemoryService`：记忆 CRUD、语义搜索、总结生成、embedding 编排 |
| `repositories.py` | `MemoryRepository`：记忆元数据 CRUD<br>`EmbeddingRepository`：向量数据读写、语义搜索查询 |
| `views.py` | REST API 视图（仅处理 HTTP 请求响应，委托给 MemoryService） |
| `serializers.py` | DRF 序列化器（输入验证 + 输出格式化） |
| `tasks.py` | Celery 异步任务：`generate_embedding`、`retry_failed_embeddings`、`generate_daily_summary`、`generate_monthly_summary` |
| `urls.py` | URL 路由注册 |

### 频率限制

memory API 端点复用现有认证频率限制中间件（认证用户 1000 次/时）。LLM 调用（压缩/总结）复用宪法 4.1 大模型 60 次/分限制，无需额外配置。

### apps/chat/ — 扩展上下文管理

| 新增/修改 | 职责 |
|-----------|------|
| `services.py` 新增 `ContextService` | 上下文窗口计算、渐进式裁剪、Safeguard 压缩编排、记忆召回注入 |
| `agent.py` 修改 | 在 Agent 执行前调用 ContextService 进行上下文管理 |

### apps/common/ — 共享工具

| 新增 | 职责 |
|------|------|
| `tokenizer.py` | tiktoken 封装：`count_tokens(text) -> int`、`count_messages_tokens(messages) -> int` |

## 依赖变更

### requirements.txt 新增

```text
tiktoken>=0.7.0
pgvector>=0.3.0
celery>=5.3.0
django-celery-beat>=2.5.0
```

### Docker 变更

- PostgreSQL 容器需安装 pgvector 扩展，或替换镜像为 `pgvector/pgvector:pg15`

### settings.py 新增配置

```python
# Celery
CELERY_BROKER_URL = 'redis://localhost:6379/2'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/2'

# Memory
MEMORY_CONTEXT_WINDOW_RATIO = 0.9      # 有效窗口比例
MEMORY_KEEP_RECENT_ROUNDS = 2          # 保留最近轮数
MEMORY_SEARCH_DEFAULT_LIMIT = 5        # 默认搜索返回条数
MEMORY_EMBEDDING_MAX_RETRIES = 3       # Embedding 最大重试次数
MEMORY_COMPRESS_LOCK_TIMEOUT = 60      # 按用户压缩锁超时（秒）
MEMORY_EMBEDDING_PENDING_TIMEOUT = 300 # pending 超时秒数（超过后视为卡死，重新投递）
MEMORY_CONTENT_MAX_LENGTH = 10000      # 记忆内容最大字符数
```

## 实施阶段

### 阶段 1：基础设施（P0）

1. pgvector 扩展安装与验证
2. Celery 配置与验证（celery.py、settings、__init__）
3. tiktoken 工具模块（`apps/common/tokenizer.py`）
4. 新增 `apps/memory/` app 骨架

### 阶段 2：数据层（P0）

5. `UserMemory` 模型 + migration
6. `UserMemoryEmbedding` 模型 + migration（含 pgvector VectorField）
7. `MemoryRepository`：记忆元数据 CRUD
8. `EmbeddingRepository`：向量数据读写 + 语义搜索

### 阶段 3：服务层 — 记忆 CRUD（P0）

9. `MemoryService.create_memory` — 创建记忆 + 投递 embedding 任务
10. `MemoryService.update_memory` — 更新记忆 + 重置 embedding
11. `MemoryService.delete_memory` — 删除记忆（级联）
12. `MemoryService.list_memories` — 列表查询
13. `MemoryService.get_memory` — 详情查询

### 阶段 4：Embedding 异步生成（P0）

14. `tasks.generate_embedding` — Celery 任务：调用 Embedding API 生成向量
15. `tasks.retry_failed_embeddings` — 定时扫描失败记录重试
16. Embedding API 客户端封装（OpenAI 兼容接口）

### 阶段 5：语义搜索（P1）

17. `MemoryService.search_memory` — 向量搜索 + 关键词降级
18. `EmbeddingRepository.vector_search` — pgvector 余弦距离查询
19. 关键词匹配实现（PostgreSQL 全文搜索）
20. 结果合并与排序

### 阶段 6：上下文管理（P0）

21. `ContextService.get_effective_window` — 动态窗口计算
22. `ContextService.prune_messages` — 渐进式裁剪
23. `ContextService.compress_messages` — Safeguard 压缩（含 Redis 锁）
24. `ContextService.build_context` — 上下文组装主流程（召回 + 裁剪 + 压缩）
25. Agent 集成：在 agent.py 中接入 ContextService

### 阶段 7：记忆总结（P2）

26. `MemoryService.summarize_and_store` — 核心总结方法
27. `tasks.generate_daily_summary` — 每日总结定时任务
28. `tasks.generate_monthly_summary` — 每月总结定时任务

### 阶段 8：API 视图层（P0）

29. 序列化器定义（MemorySerializer、MemorySearchSerializer）
30. 视图实现（MemoryViewSet）
31. URL 路由注册

### 阶段 9：测试（P0）

32. 模型测试（test_models.py）
33. 仓库层测试（test_repositories.py）
34. 服务层测试（test_services.py） — 95% 覆盖
35. API 视图测试（test_views.py）
36. Celery 任务测试（test_tasks.py）
37. 用户隔离专项测试（test_isolation.py）
38. 上下文管理测试（test_context_service.py）

## Complexity Tracking

> 无宪法违规需要 justify。

| 设计决策 | 理由 |
|---------|------|
| 新建 `apps/memory/` 独立 app | 记忆管理是独立业务域，遵循关注点分离 |
| ContextService 放在 chat app | 上下文管理是对话流程的编排层，与 chat 紧密相关 |
| Celery Broker 使用 Redis DB2 | 与 DB0（应用缓存）、DB1（Langfuse）隔离 |
| 向量维度固定 2048 | 简化实现，避免动态维度带来的 migration 和索引复杂度 |

---

*文档版本：v1.0*
*创建日期：2026-01-30*
*更新日期：2026-01-30 — analyze 修复（去16k下限/删admin.py/维度固定2048/频率限制说明/压缩锁改user_id）*
