# Tasks: 上下文与记忆管理 (M1b)

**Input**: Design documents from `/specs/004-context-memory/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/memory-api.yaml, quickstart.md

**Tests**: 规范要求测试覆盖（宪法第三条，服务层 95%），因此包含测试任务。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `backend/apps/`, `backend/core/`, `backend/tests/`
- **Common**: `backend/apps/common/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目基础设施初始化 — pgvector 扩展、Celery 配置、依赖安装、App 骨架

- [ ] T001 更新 `docker-compose.yml` 将 PostgreSQL 镜像替换为 `pgvector/pgvector:pg15` 并验证 pgvector 扩展可用
- [ ] T002 更新 `backend/requirements.txt` 添加新依赖：tiktoken>=0.7.0, pgvector>=0.3.0, celery>=5.3.0, django-celery-beat>=2.5.0，并执行 pip install（注：`pgvector` 包自带 `pgvector.django` 模块，无需额外安装 `django-pgvector`）
- [ ] T003 [P] 创建 Celery 应用配置 `backend/core/celery.py`，包含 autodiscover_tasks 和 Django settings 集成
- [ ] T004 [P] 修改 `backend/core/__init__.py` 导入 Celery app 确保 Django 启动时自动加载
- [ ] T005 修改 `backend/core/settings.py` 添加 Celery 配置（BROKER_URL=redis DB2、Beat Schedule：retry_failed_embeddings 每 5 分钟、generate_daily_summary 每天 00:00、generate_monthly_summary 每月 1 日 00:00、时区）、Memory 业务配置常量（含 MEMORY_EMBEDDING_PENDING_TIMEOUT=300、MEMORY_CONTENT_MAX_LENGTH=10000）、INSTALLED_APPS 新增 django.contrib.postgres / pgvector.django / django_celery_beat / apps.memory
- [ ] T005b 验证 ModelConfig 模型已包含 `type='embedding'` 支持、`embedding_dimensions` 字段（nullable），确认可查询到 embedding 类型配置。若无 embedding 类型的 ModelConfig 记录，在 quickstart.md 中说明需先创建
- [ ] T006 [P] 创建 tiktoken 工具模块 `backend/apps/common/tokenizer.py`，封装 count_tokens(text) -> int 和 count_messages_tokens(messages) -> int，使用 cl100k_base 编码，全局缓存编码器实例
- [ ] T007 创建 `backend/apps/memory/` Django App 骨架（__init__.py, models.py, views.py, urls.py, services.py, repositories.py, serializers.py, tasks.py），在 apps.py 中配置 name='apps.memory'

**Checkpoint**: 基础设施就绪 — pgvector 可用、Celery 可启动、tiktoken 可调用、memory app 已注册

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 数据模型和仓库层 — 所有用户故事的共享基础

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T008 创建 Django migration 启用 pgvector 扩展：在 `backend/apps/memory/migrations/0001_initial.py` 中添加 `CREATE EXTENSION IF NOT EXISTS vector` 操作
- [ ] T009 实现 UserMemory 模型 `backend/apps/memory/models.py`，包含 id/user_id(BigIntegerField, 非 ForeignKey，逻辑关联 SysUser.user_id)/type/name/content/embedding_status/retry_count/tags/importance_score/created_at/updated_at 字段，type 使用 TextChoices 枚举（memory/compaction/daily-summary/monthly-summary），embedding_status 使用 TextChoices 枚举（pending/processing/done/failed），添加 data-model.md 定义的 4 个索引
- [ ] T010 实现 UserMemoryEmbedding 模型 `backend/apps/memory/models.py`，包含 id/memory_id(FK CASCADE)/user_id(BigIntegerField)/type/name/chunk_index/chunk_text/embedding(VectorField(dimensions=2048))/created_at 字段，添加 user_id 和 memory_id 索引
- [ ] T011 生成并执行数据库迁移：`python manage.py makemigrations memory && python manage.py migrate`
- [ ] T012 [P] 实现 MemoryRepository `backend/apps/memory/repositories.py`，包含 create/get_by_id/get_by_user_id/update/delete/list_by_user/find_retryable/find_by_type_and_date/find_active_users_for_daily(date)/find_active_users_for_monthly(year, month) 方法，所有查询方法强制 user_id 参数，使用 @sync_to_async 装饰器。find_active_users_for_daily 查询当天有 compaction/message 记录的用户；find_active_users_for_monthly 查询当月有 daily-summary/message 记录的用户
- [ ] T013 [P] 实现 EmbeddingRepository `backend/apps/memory/repositories.py`，包含 create/delete_by_memory_id/vector_search(user_id, embedding, limit)/keyword_search(user_id, query, limit) 方法，vector_search 使用 pgvector CosineDistance，keyword_search 使用 Django SearchVector/SearchQuery

**Checkpoint**: 数据层就绪 — 两个模型可用、仓库层 CRUD 完备、用户隔离在仓库层强制执行

---

## Phase 3: User Story 2 — 长期记忆 CRUD (Priority: P0) 🎯 MVP

**Goal**: 提供完整的记忆增删改查 REST API，异步生成 embedding，用户间记忆严格隔离

**Independent Test**: 通过 API 直接调用 CRUD 端点，验证数据正确性、embedding 状态流转、用户隔离

> 注：US2 放在 US1 前面实施，因为 US1（上下文管理）的压缩功能需要调用记忆写入（create_memory），而 US2 是纯 CRUD 无外部依赖，更适合作为 MVP。

### Implementation for User Story 2

- [ ] T014 [P] [US2] 实现 Embedding API 客户端封装 `backend/apps/memory/services.py` 中的 EmbeddingClient 类，从 ModelConfig 获取 type='embedding' 配置、SM4 解密 API Key、调用 openai.AsyncOpenAI embeddings.create、返回向量维度必须为 2048 否则抛出异常、LLM 异常处理（遵循宪法 4.3）、Langfuse 追踪
- [ ] T015 [P] [US2] 实现 Celery 任务 generate_embedding `backend/apps/memory/tasks.py`，包含 status pending→processing→done/failed 流转、retry_count 累加、超过 3 次永久 failed、Django logging 记录失败和重试耗尽事件。写入 user_memory_embedding 时，从 UserMemory 复制 user_id/type/name 到 embedding 记录
- [ ] T016 [US2] 实现 MemoryService.create_memory `backend/apps/memory/services.py`，事务中写入 user_memory（status=pending, retry_count=0），投递 generate_embedding 异步任务，embedding 服务不可用时标记 failed 不阻塞
- [ ] T017 [US2] 实现 MemoryService.update_memory `backend/apps/memory/services.py`，验证 memory_id 属于 user_id（隔离检查），事务中更新 content + 重置 embedding_status=pending + retry_count=0，投递异步任务重新生成 embedding
- [ ] T018 [US2] 实现 MemoryService.delete_memory `backend/apps/memory/services.py`，验证 memory_id 属于 user_id，删除 user_memory 记录（FK CASCADE 自动删除 embedding）
- [ ] T019 [P] [US2] 实现 MemoryService.list_memories 和 get_memory `backend/apps/memory/services.py`，支持按 type 过滤和分页，强制 user_id 过滤
- [ ] T020 [P] [US2] 实现 Celery 定时任务 retry_failed_embeddings `backend/apps/memory/tasks.py`，扫描 embedding_status='failed' 且 retry_count<3 以及 embedding_status='pending' 超过 MEMORY_EMBEDDING_PENDING_TIMEOUT（默认 300 秒）的记录，重新投递 generate_embedding 任务
- [ ] T021 [P] [US2] 实现 DRF 序列化器 `backend/apps/memory/serializers.py`：MemoryCreateSerializer（content 必填 max_length=10000, name 可选，type 字段不暴露，硬编码为 'memory'）、MemoryUpdateSerializer（content 必填 max_length=10000）、MemoryResponseSerializer（id/type/name/content/embedding_status/created_at/updated_at）
- [ ] T022 [US2] 实现 MemoryViewSet `backend/apps/memory/views.py`，包含 list/create/retrieve/update/destroy 操作，视图层从 request.user.user_id 获取 user_id 传递给 MemoryService（禁止从请求体/查询参数接受 user_id），统一响应格式 {code, data, message}
- [ ] T023 [US2] 配置 URL 路由 `backend/apps/memory/urls.py` 注册 MemoryViewSet，在 `backend/core/urls.py` 中挂载到 /api/v1/memories/

### Tests for User Story 2

- [ ] T024 [P] [US2] 编写模型单元测试 `backend/tests/memory/test_models.py`：UserMemory 字段验证、type 枚举约束、embedding_status 默认值、UserMemoryEmbedding FK 级联删除
- [ ] T025 [P] [US2] 编写仓库层测试 `backend/tests/memory/test_repositories.py`：MemoryRepository CRUD 操作、用户隔离强制校验、EmbeddingRepository 基本读写
- [ ] T026 [US2] 编写服务层测试 `backend/tests/memory/test_services.py`：MemoryService create/update/delete/list/get 全路径覆盖，mock Celery 任务投递，mock EmbeddingClient，异常场景（不存在、不属于当前用户），目标覆盖率 95%
- [ ] T027 [P] [US2] 编写 Celery 任务测试 `backend/tests/memory/test_tasks.py`：generate_embedding 成功/失败/重试耗尽场景，retry_failed_embeddings 扫描逻辑，mock OpenAI API 调用
- [ ] T028 [US2] 编写 API 视图集成测试 `backend/tests/memory/test_views.py`：全部 6 个端点（list/create/get/update/delete + search 占位），认证校验、参数验证、响应格式验证
- [ ] T029 [US2] 编写用户隔离专项测试 `backend/tests/memory/test_isolation.py`：用户 A 创建记忆后用户 B 无法访问/修改/删除，无 user_id 查询返回错误，跨用户搜索结果隔离

**Checkpoint**: 记忆 CRUD 完整可用 — API 端点通过测试，embedding 异步生成正常，用户隔离验证通过

---

## Phase 4: User Story 3 — 语义搜索与自动召回 (Priority: P1)

**Goal**: 基于用户输入进行 pgvector 语义搜索，embedding_status!=done 降级为关键词匹配，搜索结果用于对话上下文召回

**Independent Test**: 存入已知记忆（含已完成 embedding），用语义相关查询验证召回结果正确性和延迟

### Implementation for User Story 3

- [ ] T030 [US3] 实现 EmbeddingRepository.vector_search `backend/apps/memory/repositories.py` 中的向量相似度搜索，使用 pgvector CosineDistance 按 user_id 过滤，返回 top-N 结果含相似度得分
- [ ] T031 [US3] 实现 EmbeddingRepository.keyword_search `backend/apps/memory/repositories.py` 中的 PostgreSQL 全文搜索降级，使用 SearchVector/SearchQuery（config='simple'），按 user_id 过滤 embedding_status!='done' 的记忆
- [ ] T032 [US3] 实现 MemoryService.search_memory `backend/apps/memory/services.py`，生成查询文本 embedding → 向量搜索（done 状态记忆）+ 关键词搜索（非 done 状态记忆）→ 结果合并去重按相关度排序，性能约束 <500ms
- [ ] T033 [P] [US3] 实现搜索序列化器 `backend/apps/memory/serializers.py` 新增 MemorySearchSerializer（query 必填, limit 可选默认 5）和 MemorySearchResultSerializer（继承 MemoryResponseSerializer + score + match_type）
- [ ] T034 [US3] 实现搜索 API 端点：在 MemoryViewSet `backend/apps/memory/views.py` 中添加 @action search 方法（POST /api/v1/memories/search/），委托 MemoryService.search_memory
- [ ] T035 [US3] 实现记忆自动召回方法 MemoryService.retrieve_relevant_memories `backend/apps/memory/services.py`，调用 search_memory 后格式化为上下文注入格式（system 消息），供 ContextService 使用

### Tests for User Story 3

- [ ] T036 [P] [US3] 编写语义搜索服务层测试 `backend/tests/memory/test_services.py` 追加搜索相关测试：search_memory 向量+关键词合并、空结果、用户隔离、降级场景
- [ ] T037 [US3] 编写搜索 API 集成测试 `backend/tests/memory/test_views.py` 追加 POST /memories/search/ 端点测试：正常搜索、空查询、参数验证

**Checkpoint**: 语义搜索可用 — 向量搜索 + 关键词降级通过测试，搜索延迟 < 500ms，自动召回方法可供上下文管理调用

---

## Phase 5: User Story 1 — 动态上下文窗口管理 (Priority: P0)

**Goal**: 基于模型配置动态计算上下文窗口，对话历史超出时自动裁剪，裁剪不够时 Safeguard 压缩（LLM 摘要 + Redis 锁），压缩内容存入记忆表

**Independent Test**: 构造超长对话历史，验证窗口计算、裁剪策略、压缩触发和 compaction 记忆写入

> 注：US1 依赖 US2（记忆写入）和 US3（记忆召回），因此放在 Phase 5。

### Implementation for User Story 1

- [ ] T038 [US1] 实现 ContextService.get_effective_window `backend/apps/chat/services.py` 新增 ContextService 类，从 ModelConfig 读取 max_context_window，计算有效窗口 = value * 0.9（可直接复用 ModelConfig.effective_context_window 属性），使用 tokenizer.py 的 count_tokens
- [ ] T039 [US1] 实现 ContextService.prune_messages `backend/apps/chat/services.py`，分离 system/非 system 消息，保留 system prompt + 最近 N 轮（默认 2，1 轮 = 1 对 user+assistant 消息）+ 召回记忆，从最早非保留消息开始丢弃，返回 (保留消息, 被裁剪消息)
- [ ] T040 [US1] 实现 ContextService.compress_messages `backend/apps/chat/services.py`，Redis 分布式锁（key=compress:{user_id}, timeout=60s）、未获锁等待后重新检查 token 是否仍超限、调用 LLM 生成摘要（重试 3 次，失败回退简单截断）、成功时调用 MemoryService.create_memory(type='compaction', content=摘要文本) 存入记忆表（不经过 summarize_and_store）、Langfuse 追踪 LLM 调用
- [ ] T041 [US1] 实现 ContextService.build_context `backend/apps/chat/services.py` 上下文组装主流程：调用 MemoryService.retrieve_relevant_memories 获取召回记忆 → 组装 system prompt + 召回记忆(system 消息) + 对话历史 + 用户输入 → 计算完整 prompt token 总量 → ≥ effective_window 则 prune_messages → 仍超限则 compress_messages → 返回最终消息列表
- [ ] T042 [US1] 修改 `backend/apps/chat/agent.py` 在 Agent 执行前集成 ContextService.build_context，替换现有直接传递历史消息的逻辑，确保每次 LLM 调用前都经过上下文管理

### Tests for User Story 1

- [ ] T043 [P] [US1] 编写 tokenizer 工具测试 `backend/tests/common/test_tokenizer.py`：count_tokens 精度验证、count_messages_tokens 多消息计数、空输入处理
- [ ] T044 [US1] 编写 ContextService 测试 `backend/tests/chat/test_context_service.py`：get_effective_window 正常场景、prune_messages 裁剪顺序和保留逻辑（基于完整 prompt token 对比）、compress_messages 成功/LLM 失败回退/Redis 锁（user_id 粒度）逻辑、build_context 端到端流程，mock LLM 调用和 MemoryService，目标覆盖率 95%

**Checkpoint**: 上下文管理完整可用 — 动态窗口计算准确，裁剪保留正确，压缩生成 compaction 记忆，Agent 集成正常

---

## Phase 6: User Story 4 — 记忆总结机制 (Priority: P2)

**Goal**: 实现三种记忆总结：compaction（压缩触发）、daily-summary（每日 00:00）、monthly-summary（每月 1 日），共用核心方法，支持数据来源降级

**Independent Test**: 模拟定时任务执行，验证总结生成和降级策略（有 compaction → 从 compaction 总结；无 compaction → 从 message 表；无数据 → 跳过）

### Implementation for User Story 4

- [ ] T045 [US4] 实现 MemoryService.summarize_and_store `backend/apps/memory/services.py` 核心总结方法：调用 LLM 生成摘要 → create_memory 写入指定 type 和 name → 异步生成 embedding，Langfuse 追踪 LLM 调用，Django logging 记录总结执行事件
- [ ] T046 [US4] 实现 Celery 定时任务 generate_daily_summary `backend/apps/memory/tasks.py`：遍历活跃用户 → 查 user_memory type='compaction' 当天记录 → 无数据降级到 message 表原始对话 → 仍无数据跳过 → 调用 summarize_and_store(type='daily-summary', name='daily-YYYY-MM-DD')
- [ ] T047 [US4] 实现 Celery 定时任务 generate_monthly_summary `backend/apps/memory/tasks.py`：遍历活跃用户 → 查 user_memory type='daily-summary' 当月记录 → 无数据降级到 message 表 → 仍无数据跳过 → 调用 summarize_and_store(type='monthly-summary', name='monthly-YYYY-MM')

### Tests for User Story 4

- [ ] T048 [US4] 编写记忆总结测试 `backend/tests/memory/test_services.py` 追加 summarize_and_store 测试：LLM 摘要生成、记忆写入、embedding 投递
- [ ] T049 [US4] 编写定时任务测试 `backend/tests/memory/test_tasks.py` 追加 daily/monthly summary 测试：正常执行、数据来源降级（compaction → message → 跳过）、无活跃用户跳过

**Checkpoint**: 记忆总结可用 — 三种总结类型均可触发，降级策略正确，定时任务可通过 Celery Beat 调度

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨用户故事的质量保障和最终验证

- [ ] T050 [P] 可观测性最终检查：确认所有 LLM 调用（压缩、总结、embedding）均有 Langfuse 追踪，关键事件（embedding 失败、压缩触发、总结执行、重试耗尽）均有 Django logging，日志级别符合规范（失败 WARNING+，正常 INFO）
- [ ] T051 [P] 编写性能基准测试：语义搜索端到端延迟验证（<500ms）、上下文裁剪逻辑延迟验证（构造大消息列表测试裁剪耗时）
- [ ] T052 运行全量测试并验证覆盖率：`pytest --cov=apps/memory --cov=apps/chat --cov=apps/common --cov-report=term-missing`，确认服务层 ≥ 95%、仓库层 ≥ 85%、视图层 ≥ 80%、总体 ≥ 80%
- [ ] T053 代码质量检查：运行 `black . && isort . && mypy apps/memory apps/common` 确保代码风格和类型注解合规
- [ ] T054 执行 quickstart.md 验证：按照 quickstart.md 步骤从零启动（pgvector 扩展、依赖安装、迁移、Celery 启动），确认所有 API 端点可访问
- [ ] T055 [P] 创建 `backend/apps/memory/README.md` 模块文档，包含模块职责、API 端点概览、数据模型关系说明（宪法第七条）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US2 CRUD (Phase 3)**: Depends on Foundational — 独立可实施
- **US3 Search (Phase 4)**: Depends on Foundational + US2（需要记忆数据和 embedding 存在）
- **US1 Context (Phase 5)**: Depends on US2 + US3（需要 create_memory 写入 + retrieve_relevant_memories 召回）
- **US4 Summary (Phase 6)**: Depends on US2（需要 create_memory 和 summarize_and_store）
- **Polish (Phase 7)**: Depends on all user stories complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational)
                        │
                        ▼
                  Phase 3 (US2: CRUD) ──────────────┐
                        │                           │
                        ▼                           ▼
                  Phase 4 (US3: Search)       Phase 6 (US4: Summary)
                        │
                        ▼
                  Phase 5 (US1: Context)
                        │
                        ▼
                  Phase 7 (Polish)
```

### Within Each User Story

- Models before repositories
- Repositories before services
- Services before views/endpoints
- Implementation before tests (非 TDD，因为服务层需要先确定接口)
- Story complete before moving to next priority

### Parallel Opportunities

Within Phase 1:
- T003 (celery.py) ∥ T004 (__init__.py) ∥ T006 (tokenizer.py) — 不同文件无依赖

Within Phase 2:
- T012 (MemoryRepository) ∥ T013 (EmbeddingRepository) — 同文件但独立类

Within Phase 3 (US2):
- T014 (EmbeddingClient) ∥ T015 (generate_embedding task) ∥ T019 (list/get) ∥ T020 (retry task) ∥ T021 (serializers)
- T024 (model tests) ∥ T025 (repo tests) ∥ T027 (task tests)

Within Phase 4 (US3):
- T033 (search serializer) can parallel with T030/T031

---

## Parallel Example: User Story 2 (MVP)

```bash
# Launch parallel implementation tasks:
Task T014: "Embedding API client in services.py"
Task T015: "generate_embedding Celery task in tasks.py"
Task T019: "list_memories/get_memory in services.py"
Task T020: "retry_failed_embeddings task in tasks.py"
Task T021: "DRF serializers in serializers.py"

# After services ready, sequential:
Task T016: "create_memory service"
Task T017: "update_memory service"
Task T018: "delete_memory service"
Task T022: "MemoryViewSet in views.py"
Task T023: "URL routing"

# Launch parallel test tasks:
Task T024: "Model unit tests"
Task T025: "Repository tests"
Task T027: "Celery task tests"
```

---

## Implementation Strategy

### MVP First (User Story 2 — CRUD)

1. Complete Phase 1: Setup (基础设施)
2. Complete Phase 2: Foundational (数据模型 + 仓库)
3. Complete Phase 3: US2 — 记忆 CRUD
4. **STOP and VALIDATE**: 全部 6 个 API 端点可用，embedding 异步生成正常，用户隔离通过测试
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. Add US2 (CRUD) → 记忆管理 MVP 可用 → Deploy
3. Add US3 (Search) → 语义搜索上线 → Deploy
4. Add US1 (Context) → 上下文管理集成 → Deploy（核心价值交付）
5. Add US4 (Summary) → 长期记忆质量保障 → Deploy
6. Polish → 最终质量验证

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 所有查询必须通过 user_id 过滤（R-004 不可违背）
- 视图层禁止业务逻辑，全部委托 MemoryService（宪法 1.1）
- LLM 异常处理遵循宪法 4.3（重试 3 次，特定异常不重试）
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
