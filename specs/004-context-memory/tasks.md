# Tasks: 上下文与记忆管理 (M1b)

**Input**: Design documents from `/specs/004-context-memory/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, process-model.md, behavior-model.md, rule-model.md
**Branch**: `004-context-memory`

**Tests**: 规范要求测试覆盖（宪法第三条，服务层 95%），因此包含测试任务。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4, US5, US6, US7)
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `backend/apps/`, `backend/core/`, `backend/tests/`
- **Common**: `backend/apps/common/`
- **Frontend**: `frontend/src/`

## User Story Mapping (from spec.md)

| Story | 标题 | 优先级 | Phase |
|-------|------|--------|-------|
| US1 | 分层上下文组装与动态窗口管理 | P0 | 5 |
| US2 | 优先级驱动的上下文压缩 | P0 | 7 |
| US3 | 长期记忆 CRUD | P0 | 3 |
| US4 | 语义搜索与自动召回 | P1 | 4 |
| US5 | 记忆总结机制 | P2 | 8 |
| US6 | LangGraph 多流程编排 | P0 | 6 |
| US7 | 前端上下文压缩状态提示 | P1 | 9 |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目基础设施初始化 — Docker 镜像、pgvector/pg_jieba 扩展、Celery 配置、依赖安装、App 骨架

- [X] T001 创建自定义 PostgreSQL Docker 镜像 `docker/postgres/Dockerfile`，基于 `postgres:15-alpine` 编译安装 pgvector 和 pg_jieba 扩展（→ RES-001, RES-009），更新 `docker-compose.yml` 使用自定义镜像，添加初始化脚本执行 `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_jieba;`。**退化方案**：若 pg_jieba 编译失败，退化为 PostgreSQL 默认 `simple` 配置（按空格分词，中文分词精度下降但功能不中断）
- [X] T002 更新 `backend/requirements.txt` 添加新依赖：tiktoken>=0.7.0, pgvector>=0.3.0, celery>=5.3.0, django-celery-beat>=2.5.0，并执行 pip install（注：`pgvector` 包自带 `pgvector.django` 模块，无需额外安装 `django-pgvector`）
- [X] T003 [P] 创建 Celery 应用配置 `backend/core/celery.py`，包含 autodiscover_tasks 和 Django settings 集成（→ RES-003）
- [X] T004 [P] 修改 `backend/core/__init__.py` 导入 Celery app：`from .celery import app as celery_app`
- [X] T005 修改 `backend/core/settings.py`：添加 Celery 配置（CELERY_BROKER_URL=redis://localhost:6379/2、Beat Schedule：retry_failed_embeddings 每 5 分钟、generate_daily_summary 每天 00:00、generate_monthly_summary 每月 1 日 00:00、时区 Asia/Shanghai）、Memory 业务配置常量（MEMORY_EMBEDDING_PENDING_TIMEOUT=300、MEMORY_CONTENT_MAX_LENGTH=10000、MEMORY_EMBEDDING_DIMENSION=2048、MEMORY_SEARCH_TOP_K=5、MEMORY_VECTOR_WEIGHT=0.7、MEMORY_KEYWORD_WEIGHT=0.3、MEMORY_EMBEDDING_MAX_RETRY=3、COMPRESS_LOCK_TIMEOUT=60）、INSTALLED_APPS 新增 django.contrib.postgres / django_celery_beat / apps.memory
- [X] T005b 验证 ModelConfig 模型已包含 `type='embedding'` 支持、`embedding_dimensions` 字段（nullable），确认可查询到 embedding 类型配置。若无 embedding 类型的 ModelConfig 记录，在 quickstart.md 中说明需先创建
- [X] T006 [P] 创建 tiktoken 工具模块 `backend/apps/common/tokenizer.py`，封装 count_tokens(text: str) -> int 和 count_messages_tokens(messages: list[dict]) -> int，使用 cl100k_base 编码，全局缓存编码器实例（→ RES-002, R-017）
- [X] T007 创建 `backend/apps/memory/` Django App 骨架（__init__.py, apps.py, models.py, views.py, urls.py, services.py, repositories.py, serializers.py, tasks.py, tools.py），在 apps.py 中配置 name='apps.memory'

**Checkpoint**: 基础设施就绪 — pgvector + pg_jieba 可用、Celery 可启动、tiktoken 可调用、memory app 已注册

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 数据模型和仓库层 — 所有用户故事的共享基础

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T008 创建 Django migration `backend/apps/memory/migrations/0001_initial.py`：启用 pgvector 扩展（`CREATE EXTENSION IF NOT EXISTS vector`）、创建 pg_jieba 配置（`CREATE TEXT SEARCH CONFIGURATION jiebacfg (PARSER = jieba)` 或确认扩展自带）
- [X] T009 实现 UserMemory 模型 `backend/apps/memory/models.py`，包含 id/user_id(BigIntegerField, db_index=True, 非 ForeignKey，逻辑关联 SysUser.user_id)/type(CharField + TextChoices: memory/compaction/daily-summary/monthly-summary)/name(CharField, nullable)/content(TextField)/embedding_status(CharField + TextChoices: pending/processing/done/failed, default='pending')/retry_count(IntegerField, default=0)/tags(JSONField, nullable)/importance_score(FloatField, nullable)/created_at/updated_at 字段，添加 data-model.md 定义的 6 个索引（idx_user_memory_user_id, idx_user_memory_embedding_status, idx_user_memory_type(user_id+type), idx_user_memory_retry(embedding_status+retry_count), idx_user_memory_created(user_id+created_at), idx_user_memory_content_tsv(**GIN 索引须通过 migration RunSQL 实现**：`CREATE INDEX idx_user_memory_content_tsv ON user_memory USING GIN (to_tsvector('jiebacfg', content))`，Django ORM 不原生支持自定义 tsvector config 的 GIN 索引)）
- [X] T010 实现 UserMemoryEmbedding 模型 `backend/apps/memory/models.py`，包含 id/memory_id(FK to UserMemory, on_delete=CASCADE)/user_id(BigIntegerField, db_index=True)/type(CharField)/name(CharField, nullable)/chunk_index(IntegerField, default=0)/chunk_text(TextField, nullable)/embedding(VectorField(dimensions=2048), nullable)/created_at 字段，添加 user_id 和 memory_id 索引
- [X] T011 生成并执行数据库迁移：`python manage.py makemigrations memory && python manage.py migrate`
- [X] T012 [P] 实现 MemoryRepository `backend/apps/memory/repositories.py`，包含 create/get_by_id/get_by_user_id/update/delete/list_by_user(user_id, type_filter, page, page_size)/find_retryable(max_retry=3)/find_pending_timeout(timeout_seconds)/find_by_type_and_date_range(user_id, type, start_date, end_date)/find_active_users_for_daily(date)/find_active_users_for_monthly(year, month) 方法，所有查询方法强制 user_id 参数（→ R-004），无 user_id 时抛出 ValueError。**异步策略**：仓库层方法使用同步 Django ORM，由服务层/视图层通过 `sync_to_async` 包装调用（与现有 chat app 仓库层模式一致）
- [X] T013 [P] 实现 EmbeddingRepository `backend/apps/memory/repositories.py`，包含 create/delete_by_memory_id/get_by_memory_id/vector_search(user_id, query_embedding, limit=5)/keyword_search(user_id, query_text, limit=5) **骨架方法**（方法签名 + 基础 QuerySet 过滤 + user_id 强制检查），完整的得分计算、排序逻辑和过滤条件在 Phase 4 T030/T031 中实现（→ RES-008, R-010）。同样使用同步 Django ORM + `sync_to_async` 模式

**Checkpoint**: 数据层就绪 — 两个模型可用、仓库层 CRUD 完备、用户隔离在仓库层强制执行

---

## Phase 3: User Story 3 — 长期记忆 CRUD (Priority: P0) 🎯 MVP

**Goal**: 提供完整的记忆增删改查 REST API，异步生成 embedding，用户间记忆严格隔离

**Independent Test**: 通过 API 直接调用 CRUD 端点，验证数据正确性、embedding 状态流转、用户隔离

> 注：US3（记忆 CRUD）放在最前面实施，因为它是纯 CRUD 无外部依赖，且其他用户故事（US1/US2/US4/US5/US6）都依赖记忆写入/读取能力。

### Implementation for User Story 3

- [X] T014 [P] [US3] 实现 EmbeddingClient 类 `backend/apps/memory/services.py`，从 ModelConfig 获取 type='embedding' 配置（无配置抛出 EmbeddingConfigNotFoundError）、SM4 解密 API Key、调用 openai.AsyncOpenAI client.embeddings.create、返回向量维度校验为 2048（非 2048 抛出 ValueError）、content token 超出模型输入限制时截取前 N tokens（→ R-011, R-021）、LLM 异常处理遵循宪法 4.3、Langfuse 追踪
- [X] T015 [P] [US3] 实现 Celery 任务 generate_embedding `backend/apps/memory/tasks.py`，包含 status pending→processing→done/failed 流转、retry_count 累加、超过 3 次永久 failed（→ R-013）、Django logging 记录失败（WARNING）和重试耗尽（WARNING）事件（→ R-016）。写入 user_memory_embedding 时从 UserMemory 复制 user_id/type/name 到 embedding 记录
- [X] T016 [US3] 实现 MemoryService.create_memory `backend/apps/memory/services.py`，事务中写入 user_memory（status=pending, retry_count=0）→ 投递 generate_embedding 异步任务 → embedding 服务不可用时标记 failed 不阻塞（→ R-009, R-015）。支持 type 参数（默认 'memory'，系统内部可传 compaction/daily-summary/monthly-summary）
- [X] T017 [US3] 实现 MemoryService.update_memory `backend/apps/memory/services.py`，验证 memory_id 属于 user_id（隔离检查 → R-004），事务中更新 content + 重置 embedding_status=pending + retry_count=0 → 投递异步任务重新生成 embedding。更新时旧 embedding 由异步任务处理（先写新 → 删旧）
- [X] T018 [US3] 实现 MemoryService.delete_memory `backend/apps/memory/services.py`，验证 memory_id 属于 user_id（→ R-004），删除 user_memory 记录（FK CASCADE 自动删除 embedding）
- [X] T019 [P] [US3] 实现 MemoryService.list_memories 和 get_memory `backend/apps/memory/services.py`，支持按 type 过滤和分页，强制 user_id 过滤
- [X] T020 [P] [US3] 实现 Celery 定时任务 retry_failed_embeddings `backend/apps/memory/tasks.py`，扫描 embedding_status='failed' 且 retry_count<3 以及 embedding_status='pending' 超过 MEMORY_EMBEDDING_PENDING_TIMEOUT（默认 300 秒）的记录，重新投递 generate_embedding 任务（→ R-006, R-012）
- [X] T021 [P] [US3] 实现 DRF 序列化器 `backend/apps/memory/serializers.py`：MemoryCreateSerializer（content 必填 max_length=10000 → R-021, name 可选，type 字段不暴露由后端硬编码为 'memory' → R-008）、MemoryUpdateSerializer（content 必填 max_length=10000）、MemoryResponseSerializer（id/type/name/content/embedding_status/tags/created_at/updated_at）
- [X] T022 [US3] 实现 MemoryViewSet `backend/apps/memory/views.py`，包含 list/create/retrieve/update/destroy 操作，配置 permission_classes=[IsAuthenticated] + 对象级权限检查（retrieve/update/destroy 验证资源属于当前用户 → 宪法 §4.1），视图层从 request.user.user_id 获取 user_id 传递给 MemoryService（禁止从请求体/查询参数接受 user_id → R-004），统一响应格式 {code, data, message}
- [X] T023 [US3] 配置 URL 路由 `backend/apps/memory/urls.py` 注册 MemoryViewSet，在 `backend/core/urls.py` 中挂载到 /api/v1/memories/

### Tests for User Story 3

- [X] T024 [P] [US3] 编写模型单元测试 `backend/tests/memory/test_models.py`：UserMemory 字段验证、type TextChoices 枚举约束、embedding_status 默认值 'pending'、UserMemoryEmbedding FK CASCADE 级联删除验证
- [X] T025 [P] [US3] 编写仓库层测试 `backend/tests/memory/test_repositories.py`：MemoryRepository CRUD 操作、用户隔离强制校验（无 user_id 抛 ValueError）、EmbeddingRepository 基本读写、find_retryable/find_pending_timeout 查询逻辑
- [X] T026 [US3] 编写服务层测试 `backend/tests/memory/test_services.py`：MemoryService create/update/delete/list/get 全路径覆盖，mock Celery 任务投递，mock EmbeddingClient，异常场景（记忆不存在、不属于当前用户、EmbeddingConfigNotFoundError），embedding 服务不可用降级场景，**并发 CRUD 场景**（同一用户同时创建/更新/删除的数据一致性 → NFR-003），目标覆盖率 ≥ 95%
- [X] T027 [P] [US3] 编写 Celery 任务测试 `backend/tests/memory/test_tasks.py`：generate_embedding 成功/失败/重试耗尽场景、维度校验失败、content token 截取、retry_failed_embeddings 扫描逻辑，mock OpenAI API 调用
- [X] T028 [US3] 编写 API 视图集成测试 `backend/tests/memory/test_views.py`：全部 5 个端点（list/create/get/update/delete），认证校验、参数验证（content 超长拒绝）、响应格式验证、user_id 不可从请求传入
- [X] T029 [US3] 编写用户隔离专项测试 `backend/tests/memory/test_isolation.py`：用户 A 创建记忆后用户 B 无法访问/修改/删除，无 user_id 查询返回错误，跨用户搜索结果隔离，服务层 + 仓库层 + 视图层三层验证

**Checkpoint**: 记忆 CRUD 完整可用 — API 端点通过测试，embedding 异步生成正常，用户隔离验证通过

---

## Phase 4: User Story 4 — 语义搜索与自动召回 (Priority: P1)

**Goal**: 基于用户输入进行 pgvector 语义搜索 + pg_jieba 关键词混合检索，embedding_status!=done 降级为关键词匹配，搜索结果用于对话上下文召回

**Independent Test**: 存入已知记忆（含已完成 embedding），用语义相关查询验证召回结果正确性和延迟 <500ms

### Implementation for User Story 4

- [X] T030 [US4] 完善 EmbeddingRepository.vector_search `backend/apps/memory/repositories.py`（在 T013 骨架基础上实现完整逻辑）：CosineDistance 计算相似度得分、JOIN user_memory 过滤 embedding_status='done'、按 user_id 过滤、按相似度降序排列、返回 (memory_id, similarity_score) 列表（→ R-005, R-010）
- [X] T031 [US4] 完善 EmbeddingRepository.keyword_search `backend/apps/memory/repositories.py`（在 T013 骨架基础上实现完整逻辑）：使用 SearchVector('content', config='jiebacfg') + SearchQuery(query, config='jiebacfg')、按 user_id 过滤、查询所有记忆（不限 embedding_status，done 状态记忆同时参与关键词匹配）、SearchRank 计算匹配得分、返回 (memory_id, rank_score) 列表（→ RES-008, R-005）
- [X] T032 [US4] 实现 MemoryService.search_memory `backend/apps/memory/services.py`：调用 EmbeddingClient 生成查询文本 embedding → vector_search（done 状态记忆，返回 vector_score）+ keyword_search（所有记忆不限 status，返回 keyword_score）→ 合并：同一 memory_id 出现在两个结果中时 final_score = vector_score × 0.7 + keyword_score × 0.3；仅出现在 vector_search 中时 final_score = vector_score × 0.7；仅出现在 keyword_search 中时 final_score = keyword_score × 0.3 → 去重按 final_score 降序排序 → 最多返回 5 条（→ R-010）。EmbeddingClient 不可用时回退为纯关键词搜索
- [X] T033 [P] [US4] 实现搜索序列化器 `backend/apps/memory/serializers.py` 新增 MemorySearchSerializer（query 必填, limit 可选默认 5 最大 20）和 MemorySearchResultSerializer（继承 MemoryResponseSerializer + score + match_type 字段）
- [X] T034 [US4] 实现搜索 API 端点：在 MemoryViewSet `backend/apps/memory/views.py` 中添加 @action(detail=False, methods=['post']) search 方法（POST /api/v1/memories/search/），委托 MemoryService.search_memory
- [X] T035 [US4] 实现记忆自动召回方法 MemoryService.retrieve_relevant_memories `backend/apps/memory/services.py`，调用 search_memory 后格式化为上下文注入格式（system 消息字符串），注入位置：层级 2.b — system prompt(1) 和模板(2.a) 之后、工具内容(2.c) 之前（→ behavior-model §6）

### Tests for User Story 4

- [X] T036 [P] [US4] 编写语义搜索服务层测试 `backend/tests/memory/test_services.py` 追加搜索相关测试：search_memory 向量+关键词混合合并（权重 0.7/0.3）、空结果、用户隔离、降级场景（embedding 不可用回退纯关键词）、TopK=5 限制
- [X] T037 [US4] 编写搜索 API 集成测试 `backend/tests/memory/test_views.py` 追加 POST /memories/search/ 端点测试：正常搜索、空查询、参数验证（limit 范围）、用户隔离

**Checkpoint**: 语义搜索可用 — 混合检索（向量 0.7 + 关键词 0.3）通过测试，搜索延迟 < 500ms，自动召回方法可供上下文管理调用

---

## Phase 5: User Story 1 — 分层上下文组装与动态窗口管理 (Priority: P0)

**Goal**: 实现 PromptBuilder 分层组装引擎和 ContextService 动态窗口管理，基于模型配置计算有效窗口，超限时触发压缩流程

**Independent Test**: 构造不同大小的各段内容，验证组装顺序、token 计算、有效窗口计算和压缩触发是否正确

> 注：US1 依赖 US3（记忆写入 create_memory）和 US4（记忆召回 retrieve_relevant_memories）。

### Implementation for User Story 1

- [X] T038 [P] [US1] 创建 PromptBuilder 动态 prompt 模板系统 `backend/apps/chat/prompts.py`，包含：PromptConfig 配置类（user_id, model_config, keep_recent_rounds=2）、PromptBuilder 构造函数接受 config: PromptConfig, chat_repository: ChatRepository, memory_service: MemoryService 三个依赖注入参数（宪法 1.1 关注点分离，不直接操作 ORM）、PromptModule 枚举（BASE/REASONING/TOOL_USAGE/CODE_ASSIST/CREATIVE_WRITING/DATA_ANALYSIS → FR-014）、PromptRegistry 模块管理器（含 register_custom_module(name, content) 运行时扩展 → behavior-model §1）
- [X] T039 [P] [US1] 实现 PromptBuilder 核心 6 个 build 方法 `backend/apps/chat/prompts.py`：build_system_prompt(modules) → 层级 1（~2k tokens）、build_template_block() → 层级 2.a（~1k tokens）、build_memory_block(user_id, user_message) → 层级 2.b（调用 retrieve_relevant_memories，格式化为独立 system 消息）、build_tool_context(tools) → 层级 2.c、build_conversation_history(user_id, limit) → 层级 2.d（通过注入的 ChatRepository.get_recent_messages 查询 message 表，按 user_id 过滤，取最近 limit 条 user/assistant 消息，按 created_at 升序）、build_messages() / build_messages_for_langchain() → 最终消息列表（→ behavior-model §1）。（注：build_memory_block 依赖 US4 T035 的 retrieve_relevant_memories，实现时若 US4 尚未完成，mock 此方法返回空列表）
- [X] T040 [P] [US1] 实现 PromptBuilder 固定话术模块 `backend/apps/chat/prompts.py`：BASE 模块（语言匹配、Markdown 格式化、诚实性约束、安全隐私保护、prompt 泄露防御 → FR-014）、TOOL_USAGE 模块（工具调用原则和规范）、记忆参考引导话术
- [X] T041 [P] [US1] 实现四套专用 Prompt 模板 `backend/apps/chat/prompts.py`：COMPACTION_PROMPT_TEMPLATE（对话压缩摘要 → behavior-model §7）、DAILY_SUMMARY_PROMPT_TEMPLATE（每日记忆总结）、MONTHLY_SUMMARY_PROMPT_TEMPLATE（每月记忆总结）、CRONMEM_PROMPT_TEMPLATE（定时事实抽取与打标，参考 mem0-prompt-reference.md → FR-014）
- [X] T042 [US1] 实现 PromptBuilder Token 裁剪优先级逻辑 `backend/apps/chat/prompts.py`：全部加载后调用 tokenizer 计算总 token 数（→ R-017）。裁剪优先级使用 Level 编号（**L 越小越先被压缩**，与 spec.md "重要性从低到高 d→c→b" 一致）：L0-PROTECTED（基础 system prompt 1 + 模板 2.a + 用户输入 2.e，不可丢弃）、L1-FIRST（前对话 2.d，最先压缩）、L2-SECOND（工具内容 2.c，其次压缩）、L3-LAST（记忆内容 2.b，最后压缩）。总 token 超限时按 L1→L2→L3 顺序触发 compress_context 流程（→ R-002, R-003）。**注意**：此处 L0~L3 对应 spec.md FR-014 中的裁剪级别命名
- [X] T043 [US1] 实现 ContextService `backend/apps/chat/services/context_service.py`，包含 get_effective_window(model_config) → int（= max_context_window × 0.9，<10000 拒绝 → R-001）、check_token_limit(messages, effective_window) → bool、build_context(user_id, user_message, model_config) → list[dict]（调用 PromptBuilder 组装 → 检查总 token → 超限则触发压缩编排 → 返回最终消息列表）
- [X] T044 [US1] 修改 `backend/apps/chat/services/agent_service.py`，在 Agent 执行前集成 ContextService.build_context + PromptBuilder.build_messages_for_langchain()，替换现有直接传递历史消息的逻辑，确保每次 LLM 调用前都经过上下文管理和 prompt 组装

### Tests for User Story 1

- [X] T045 [P] [US1] 编写 tokenizer 工具测试 `backend/tests/common/test_tokenizer.py`：count_tokens 精度验证（已知文本对比）、count_messages_tokens 多消息计数、空输入处理、中文文本处理
- [X] T046 [P] [US1] 编写 PromptBuilder 测试 `backend/tests/chat/test_prompts.py`：PromptConfig 默认值验证、PromptModule 枚举完整性（6 个模块）、PromptRegistry 模块注册/启用/禁用/自定义模块、build_system_prompt 各模块组装（~2k tokens 范围验证）、build_template_block 内容验证（必须包含三项：输出格式规范、回复长度引导、对话上下文窗口声明占位符）、build_memory_block 有/无记忆场景、build_tool_context 工具定义注入、build_conversation_history 轮数限制、build_messages 最终消息列表分层顺序验证（system(1) → system(2.a) → system(2.b) → system(2.c) → user/assistant(2.d) → user(2.e)）、build_messages_for_langchain 格式转换、Token 裁剪优先级（P0~P3）逻辑验证、四套专用模板内容完整性，目标覆盖率 ≥ 95%
- [X] T047 [US1] 编写 ContextService 测试 `backend/tests/chat/test_context_service.py`：get_effective_window 正常场景和 <10000 拒绝场景、check_token_limit 超限/未超限、build_context 端到端流程（含 PromptBuilder 集成，mock MemoryService），目标覆盖率 ≥ 95%

**Checkpoint**: 上下文组装完整可用 — 动态窗口计算准确，PromptBuilder 分层组装正确，Agent 集成正常

---

## Phase 6: User Story 6 — LangGraph 多流程编排 (Priority: P0)

**Goal**: 实现 LangGraph 四流程工厂（chat/context/memory/cronMem），各流程工具集严格隔离

**Independent Test**: 独立启动每个流程，验证工具集限制和流程正确性

> 注：US6 依赖 US1（PromptBuilder）和 US3（记忆工具依赖 MemoryService），为 US2（压缩编排）提供流程执行能力。

### Implementation for User Story 6

- [X] T048 [P] [US6] 实现上下文工具集 `backend/apps/chat/tools.py`：context_compact(content: str) → str（LLM 压缩总结）、context_extract(content: str, query: str) → str（片段抽取）、context_prune(content: str) → str（删除剪枝），使用 @tool 装饰器注册为 LangGraph 工具（→ FR-004, behavior-model §2）
- [X] T049 [P] [US6] 实现记忆工具集 `backend/apps/memory/tools.py`：mem_search(user_id, query, limit=5)、mem_cache(user_id, content, name=None)、mem_update(user_id, memory_ids, updates)、mem_delete(user_id, memory_ids)，委托 MemoryService 执行，使用 @tool 装饰器注册（→ FR-005, behavior-model §4）
- [X] T050 [US6] 修改 `backend/apps/chat/agent.py` 新增 create_context_agent(tools) 工厂函数 — context 流程 StateGraph，仅注册上下文工具集（contextCompact/contextExtract/contextPrune），流程：Agent → Tool → End（→ R-018, RES-012）
- [X] T051 [US6] 修改 `backend/apps/chat/agent.py` 新增 create_memory_agent(tools) 工厂函数 — memory 流程 StateGraph，仅注册记忆工具集（memSearch/memCache/memUpdate/memDelete），流程：Agent → Tool → End（→ R-018）
- [X] T052 [US6] 修改 `backend/apps/chat/agent.py` 新增 create_cronmem_agent() 工厂函数 — cronMem 流程 StateGraph，无工具注册，仅 Agent → End，使用 CRONMEM_PROMPT_TEMPLATE 专用 prompt（→ R-018, behavior-model §3）
- [X] T053 [US6] 修改现有 chat 流程在 `backend/apps/chat/agent.py` 中：将记忆工具集（memSearch/memCache/memUpdate/memDelete）注册到 chat Agent 的 tools 列表中。对话工具（python repl / bravo search / home assistant）本期不实现，仅预留注册接口供后续特性扩展（→ R-018, FR-006）。**预留方式**：chat 流程工厂函数接受 `extra_tools: list` 参数（默认空列表），当前仅传入记忆工具集，后续特性通过此参数注入对话工具，无需修改工厂函数签名
- [X] T054 [US6] 验证四流程工具集隔离：chat 流程不可调用上下文工具、context 流程不可调用记忆工具、memory 流程不可调用上下文工具、cronMem 流程无工具可用（→ R-018）

### Tests for User Story 6

- [X] T055 [P] [US6] 编写上下文工具测试 `backend/tests/chat/test_tools.py`：context_compact/context_extract/context_prune 各工具输入输出验证，mock LLM 调用
- [X] T056 [P] [US6] 编写记忆工具测试 `backend/tests/memory/test_tools.py`：mem_search/mem_cache/mem_update/mem_delete 各工具委托 MemoryService 验证，user_id 传递正确性
- [X] T057 [US6] 编写 LangGraph 流程工厂集成测试 `backend/tests/chat/test_agent.py`：四个工厂函数创建的 Agent 各自工具集正确、不越界，context 流程仅有 3 个上下文工具、memory 流程仅有 4 个记忆工具、cronMem 流程无工具

**Checkpoint**: LangGraph 四流程可用 — 各流程工厂正常创建，工具集严格隔离通过测试

---

## Phase 7: User Story 2 — 优先级驱动的上下文压缩 (Priority: P0)

**Goal**: 实现优先级压缩编排（d → c → b → 截断），Redis 分布式锁并发控制，LLM 失败回退简单截断

**Independent Test**: 构造超长上下文，验证压缩顺序（d → c → b）、Redis 锁获取/等待、LLM 失败回退、compaction 记忆写入

> 注：US2 依赖 US1（ContextService）、US3（create_memory）、US6（context/memory 流程），是整个系统的核心编排。

### Implementation for User Story 2

- [X] T058 [US2] 实现 ContextService.compress_context `backend/apps/chat/services/context_service.py`，完整压缩编排流程（→ process-model §2, behavior-model §2）：
  1. 获取 Redis 分布式锁（key=compress:{user_id}, timeout=60s → RES-005）
  1a. Redis 锁获取异常（Redis 不可用）时：降级为无锁执行压缩流程，记录 WARNING 日志，保证对话不中断
  2. 未获锁：等待锁释放后重新检查 token 是否仍超限
  3. 发送 SSE context_compacting 事件（→ R-020）
  4. 第一步：调用 context 流程处理前对话(2.d)，超长直接截断
  5. 检查是否仍超限 → 第二步：调用 context 流程处理工具内容(2.c)
  6. 检查是否仍超限 → 第三步：调用 memory 流程处理记忆内容(2.b)
  7. 检查是否仍超限 → 第四步：直接截断至有效窗口大小
  8. 成功压缩时：使用 COMPACTION_PROMPT_TEMPLATE 调用 LLM 对压缩后的对话内容生成摘要，再调用 MemoryService.create_memory(type='compaction', content=摘要) 存入记忆（→ R-014: 回退截断不生成 compaction，behavior-model §7 调用关系说明）
  9. 发送 SSE context_compacted 事件
  10. 释放 Redis 锁
- [X] T059 [US2] 实现 LLM 压缩失败回退逻辑 `backend/apps/chat/services/context_service.py`：LLM 调用重试 3 次 → 全部失败 → 回退简单截断（丢弃最早消息）→ 回退截断不生成 compaction 记忆 → 保证对话不中断（→ R-014）
- [X] T060 [US2] 集成压缩编排到 ContextService.build_context `backend/apps/chat/services/context_service.py`：token 超限检查后调用 compress_context，Langfuse 追踪 LLM 压缩调用（→ R-016），Django logging 记录压缩触发事件（INFO）
- [X] T061 [US2] 实现安全兜底逻辑 `backend/apps/chat/services/context_service.py`：10% buffer 容纳中间过程超限，超过 100% 最大窗口直接截断不报错终止（→ R-019, FR-015）

### Tests for User Story 2

- [X] T062 [US2] 编写压缩编排测试 `backend/tests/chat/test_context_service.py` 追加 compress_context 测试：d → c → b 压缩顺序验证、仅压缩 d 即满足场景、d+c 即满足场景、d+c+b 后仍超限截断场景、Redis 锁获取/等待/重新检查逻辑（user_id 粒度）、Redis 锁获取异常（Redis 不可用）时降级为无锁执行压缩（保证对话不中断）、LLM 失败回退简单截断、回退不生成 compaction 记忆、compaction 记忆正确写入（type='compaction'），mock LangGraph context/memory 流程，目标覆盖率 ≥ 95%
- [X] T063 [US2] 编写安全兜底测试 `backend/tests/chat/test_context_service.py` 追加：10% buffer 场景验证、超过 100% 直接截断不抛异常

**Checkpoint**: 上下文压缩完整可用 — 优先级压缩顺序正确，Redis 锁并发控制正常，LLM 失败回退可靠，compaction 记忆写入验证

---

## Phase 8: User Story 5 — 记忆总结机制 (Priority: P2)

**Goal**: 实现三种记忆总结：compaction（压缩触发，已在 US2 T058 中实现写入）、daily-summary（每日 00:00）、monthly-summary（每月 1 日），共用核心方法 summarize_and_store，支持数据来源降级

**Independent Test**: 模拟定时任务执行，验证总结生成和降级策略（有 compaction → 从 compaction 总结；无 compaction → 从 message 表；无数据 → 跳过）

### Implementation for User Story 5

- [X] T064 [US5] 实现 MemoryService.summarize_and_store `backend/apps/memory/services.py` 核心总结方法：调用 cronMem 流程（LangGraph create_cronmem_agent）进行事实抽取和记忆打标。**cronMem 输出格式**：CRONMEM_PROMPT_TEMPLATE 中指示 LLM 以 JSON 格式输出 `{"content": "...", "tags": [...], "date": "YYYY-MM-DD"}`，服务层使用 `json.loads()` 解析（解析失败时将整个 LLM 输出作为 content，tags 设为空列表）。解析后调用 create_memory 写入指定 type 和 name → 异步生成 embedding。Langfuse 追踪 LLM 调用（→ R-016），Django logging 记录总结执行事件（INFO）。LLM 调用失败重试 3 次后跳过（→ R-022），无数据时不生成空总结（→ R-007）
- [X] T065 [US5] 实现 Celery 定时任务 generate_daily_summary `backend/apps/memory/tasks.py`：查找活跃用户（当天有新 compaction 记忆或新 message 记录 → R-007）→ 遍历每个用户 → 查 user_memory type='compaction' 当天记录 → 无数据降级到 message 表原始对话 → 仍无数据跳过 → 调用 summarize_and_store(type='daily-summary', name='daily-YYYY-MM-DD')
- [X] T066 [US5] 实现 Celery 定时任务 generate_monthly_summary `backend/apps/memory/tasks.py`：查找活跃用户（当月有 daily-summary 记忆或新 message 记录 → R-007）→ 遍历每个用户 → 查 user_memory type='daily-summary' 当月记录 → 无数据降级到 message 表 → 仍无数据跳过 → 调用 summarize_and_store(type='monthly-summary', name='monthly-YYYY-MM')

### Tests for User Story 5

- [X] T067 [US5] 编写记忆总结测试 `backend/tests/memory/test_services.py` 追加 summarize_and_store 测试：cronMem 流程调用、LLM 摘要生成、记忆写入验证、embedding 投递、LLM 失败重试 3 次后跳过、无数据不生成空总结
- [X] T068 [US5] 编写定时任务测试 `backend/tests/memory/test_tasks.py` 追加 daily/monthly summary 测试：正常执行、数据来源降级（compaction → message → 跳过、daily-summary → message → 跳过）、活跃用户判定逻辑、无活跃用户跳过、多活跃用户全覆盖断言（构造 3 个活跃用户，验证全部处理、无遗漏）

**Checkpoint**: 记忆总结可用 — 三种总结类型均可触发，降级策略正确，定时任务可通过 Celery Beat 调度

---

## Phase 9: User Story 7 — 前端上下文压缩状态提示 (Priority: P1)

**Goal**: 前端实时显示上下文压缩状态，复用现有 SSE 流

**Independent Test**: 触发压缩操作，验证前端状态变化（显示/隐藏"正在压缩上下文"）

### Implementation for User Story 7

- [X] T069 [US7] 修改后端 SSE 模块 `backend/apps/chat/sse.py`，新增 context_compacting 和 context_compacted 事件类型发送方法，复用现有对话 SSE 流不开设独立通道（→ R-020, FR-016）。**事件 data 格式**：`{"type": "context_compacting", "data": {}}` 和 `{"type": "context_compacted", "data": {}}`（与现有 content/done/error 事件格式保持一致，data 为空对象）
- [X] T070 [US7] 修改前端类型定义 `frontend/src/types/index.ts`，ChatStreamEvent 新增 'context_compacting' | 'context_compacted' 事件类型
- [X] T071 [US7] 修改前端状态管理 `frontend/src/stores/chatStore.ts`，新增 isCompacting: boolean 状态字段和 setCompacting(value: boolean) action
- [X] T072 [US7] 修改前端 SSE 处理 `frontend/src/hooks/useChatStream.ts`，处理 context_compacting 事件（setCompacting(true)）和 context_compacted 事件（setCompacting(false)）。SSE 连接断开重连或用户切换会话返回时，isCompacting 状态跟随 SSE 流重新接收——若压缩仍在进行中，后端会在 SSE 流恢复后继续推送 context_compacted 事件；若用户在压缩完成后才返回，状态默认为 false
- [X] T073 [US7] 修改前端对话组件 `frontend/src/components/chat/MessageList.tsx`，对话框左下角根据 isCompacting 状态显示/隐藏"正在压缩上下文"状态标识

### Tests for User Story 7

- [X] T074 [US7] 编写后端 SSE 事件测试：验证 context_compacting/context_compacted 事件格式正确，复用现有 SSE 流
- [X] T075 [US7] 前端手动验证：触发压缩 → 显示提示 → 压缩完成 → 提示消失 → 切换会话返回仍显示

**Checkpoint**: 前端压缩状态提示可用 — SSE 事件正确推送，前端状态正确显示/隐藏

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: 跨用户故事的质量保障和最终验证

- [X] T076 [P] 可观测性最终检查：确认所有 LLM 调用（压缩摘要、记忆总结 daily/monthly、cronMem 事实抽取、embedding 生成）均有 Langfuse 追踪（→ R-016），关键事件均有 Django logging：embedding 失败（WARNING）、压缩触发（INFO）、定时总结执行（INFO）、重试耗尽（WARNING）、EmbeddingConfigNotFoundError（WARNING），日志级别符合规范。**同时验证** memory API 请求/响应已通过现有 Django 中间件记录（→ 宪法 §6.2 "必须记录 API 请求/响应"）
- [X] T077 [P] 编写性能基准测试：语义搜索端到端延迟验证（<500ms → R-010）、上下文裁剪/压缩额外延迟验证（<500ms 不含 LLM 等待 → NFR-001）、构造大消息列表测试 tokenizer 计算延迟
- [X] T078 运行全量测试并验证覆盖率：`pytest --cov=apps/memory --cov=apps/chat --cov=apps/common --cov-report=term-missing`，确认服务层 ≥ 95%、仓库层 ≥ 85%、视图层 ≥ 80%、总体 ≥ 80%
- [X] T079 代码质量检查：运行 `black . && isort . && mypy apps/memory apps/chat/services apps/common` 确保代码风格和类型注解合规（所有公共函数有类型注解）
- [X] T080 执行 quickstart.md 验证：按照 quickstart.md 步骤从零启动（pgvector + pg_jieba 扩展、依赖安装、迁移、Celery Worker + Beat 启动），确认所有 API 端点可访问、embedding 异步生成正常、定时任务可触发
- [X] T081 [P] 端到端流程验证：发送超长对话消息 → 触发压缩 → SSE 状态推送 → compaction 记忆写入 → 下次对话记忆自动召回 → 验证完整链路
- [X] T081b [P] 异常兜底测试：验证上下文处理过程中非 LLM 异常不中断对话 — PromptBuilder 构建抛异常时回退到最小上下文（仅 system prompt + 用户输入）继续对话、tiktoken 编码异常时使用字符数估算降级、MemoryService.retrieve_relevant_memories 异常时跳过记忆注入继续对话、ContextService.compress_context 内部异常时跳过压缩直接截断继续对话（→ NFR-005）
- [X] T082 [P] 创建 `backend/apps/memory/README.md` 模块文档（→ 宪法第七条），包含模块职责、API 端点概览（6 个端点）、数据模型关系说明（user_memory ↔ user_memory_embedding）、Celery 任务列表、配置常量说明

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US3 CRUD (Phase 3)**: Depends on Foundational — 独立可实施，MVP 基础
- **US4 Search (Phase 4)**: Depends on US3（需要记忆数据和 embedding 存在）
- **US1 Context (Phase 5)**: Depends on US3 + US4（需要 create_memory 写入 + retrieve_relevant_memories 召回）
- **US6 LangGraph (Phase 6)**: Depends on US1（PromptBuilder、专用模板）+ US3（记忆工具依赖 MemoryService）
- **US2 Compress (Phase 7)**: Depends on US1 + US6（需要 ContextService + context/memory 流程）
- **US5 Summary (Phase 8)**: Depends on US3 + US6（需要 create_memory + cronMem 流程）
- **US7 Frontend (Phase 9)**: Depends on US2（需要后端 SSE 事件已实现）
- **Polish (Phase 10)**: Depends on all user stories complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational)
                        │
                        ▼
                  Phase 3 (US3: CRUD) ────────────────────────┐
                        │                                     │
                        ▼                                     │
                  Phase 4 (US4: Search)                       │
                        │                                     │
                        ▼                                     │
                  Phase 5 (US1: Context + PromptBuilder)      │
                        │                                     │
                        ├────────────────┐                    │
                        ▼                ▼                    ▼
                  Phase 6 (US6: LangGraph 四流程)       Phase 8 (US5: Summary)*
                        │
                        ▼
                  Phase 7 (US2: 压缩编排)
                        │
                        ▼
                  Phase 9 (US7: 前端状态)
                        │
                        ▼
                  Phase 10 (Polish)

* Phase 8 (US5) 可在 Phase 6 完成后与 Phase 7 并行实施
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

Within Phase 3 (US3):
- T014 (EmbeddingClient) ∥ T015 (generate_embedding task) ∥ T019 (list/get) ∥ T020 (retry task) ∥ T021 (serializers)
- T024 (model tests) ∥ T025 (repo tests) ∥ T027 (task tests)

Within Phase 5 (US1):
- T038 (PromptBuilder 框架) ∥ T039 (build 方法) ∥ T040 (固定话术) ∥ T041 (专用模板)
- T045 (tokenizer tests) ∥ T046 (PromptBuilder tests)

Within Phase 6 (US6):
- T048 (上下文工具) ∥ T049 (记忆工具)
- T055 (上下文工具测试) ∥ T056 (记忆工具测试)

Phase 7 (US2) ∥ Phase 8 (US5) — 可并行实施

---

## Parallel Example: User Story 3 (MVP)

```bash
# Launch parallel implementation tasks:
Task T014: "EmbeddingClient in services.py"
Task T015: "generate_embedding Celery task in tasks.py"
Task T019: "list_memories/get_memory in services.py"
Task T020: "retry_failed_embeddings task in tasks.py"
Task T021: "DRF serializers in serializers.py"

# After parallel tasks ready, sequential:
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

### MVP First (User Story 3 — CRUD)

1. Complete Phase 1: Setup (基础设施)
2. Complete Phase 2: Foundational (数据模型 + 仓库)
3. Complete Phase 3: US3 — 记忆 CRUD
4. **STOP and VALIDATE**: 全部 API 端点可用，embedding 异步生成正常，用户隔离通过测试
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. Add US3 (CRUD) → 记忆管理 MVP 可用 → Deploy
3. Add US4 (Search) → 语义搜索上线 → Deploy
4. Add US1 (Context) → PromptBuilder + 上下文管理 → Deploy
5. Add US6 (LangGraph) → 四流程编排上线 → Deploy
6. Add US2 (Compress) → 上下文压缩编排上线（核心价值交付）→ Deploy
7. Add US5 (Summary) + US7 (Frontend) → 长期记忆质量 + 用户体验 → Deploy
8. Polish → 最终质量验证

---

## Summary

| 指标 | 数值 |
|------|------|
| **总任务数** | 84（含 T005b, T081b） |
| Phase 1 (Setup) | 8 |
| Phase 2 (Foundational) | 6 |
| Phase 3 (US3: CRUD) | 16 (10 impl + 6 test) |
| Phase 4 (US4: Search) | 8 (6 impl + 2 test) |
| Phase 5 (US1: Context) | 10 (7 impl + 3 test) |
| Phase 6 (US6: LangGraph) | 10 (7 impl + 3 test) |
| Phase 7 (US2: Compress) | 6 (4 impl + 2 test) |
| Phase 8 (US5: Summary) | 5 (3 impl + 2 test) |
| Phase 9 (US7: Frontend) | 7 (5 impl + 2 test) |
| Phase 10 (Polish) | 7 |
| **并行机会** | Phase 1 (3组)、Phase 2 (1组)、Phase 3 (2组)、Phase 5 (2组)、Phase 6 (2组)、Phase 7∥8 |
| **MVP 范围** | Phase 1~3 (US3: 记忆 CRUD) |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 所有查询必须通过 user_id 过滤（R-004 不可违背）
- 视图层禁止业务逻辑，全部委托 Service 层（宪法 1.1）
- LLM 异常处理遵循宪法 4.3（重试 3 次，特定异常不重试）
- 关键词匹配必须使用 pg_jieba 中文分词（config='jiebacfg'）
- 向量维度固定 2048，写入时校验
- 隔离粒度永远按 user_id，不存在"会话粒度"
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
