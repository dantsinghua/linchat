# 技术研究 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-30

---

## RES-001：pgvector 集成方案

**问题**：项目当前未使用 pgvector，需确定安装和集成方式。

**决策**：使用 `pgvector` PyPI 包（Python 绑定 + Django 集成，自带 `pgvector.django` 模块）

**理由**：
- `pgvector` 包自带 `pgvector.django` 模块，提供 Django ORM 原生支持，包括 `VectorField`、`L2Distance`、`CosineDistance` 等查询表达式
- 与现有 Django ORM 模式一致，符合宪法 1.3 "禁止原生 SQL" 原则
- PostgreSQL 15（项目使用版本）完全支持 pgvector 扩展

**替代方案**：
- 原生 SQL + psycopg2：违反宪法"禁止原生 SQL"原则，排除
- SQLAlchemy + pgvector：引入额外 ORM，增加复杂度，排除

**实施要点**：
1. Docker PostgreSQL 容器中安装 pgvector 扩展：`CREATE EXTENSION IF NOT EXISTS vector;`
2. 添加依赖：`pgvector>=0.3.0`（自带 Django 集成，无需额外 django-pgvector）
3. Django migration 中创建扩展
4. 向量维度动态读取自 `ModelConfig.embedding_dimensions`

---

## RES-002：tiktoken 集成方案

**问题**：项目当前未依赖 tiktoken，需确定集成方式。

**决策**：直接添加 `tiktoken` 依赖，使用 `cl100k_base` 编码

**理由**：
- tiktoken 是 OpenAI 官方 token 计数库，精度最高
- `cl100k_base` 编码覆盖 GPT-4/GPT-3.5-turbo 等主流模型
- 规范明确指定使用此方案（spec.md FR-001、clarification 2026-01-30）

**替代方案**：
- 字符估算（chars/4）：精度不够，可能导致上下文溢出，排除
- transformers tokenizer：依赖过重，排除

**实施要点**：
1. 添加依赖：`tiktoken>=0.7.0`
2. 创建工具模块 `apps/common/tokenizer.py`，封装 token 计数函数
3. 全局缓存编码器实例，避免重复初始化

---

## RES-003：Celery 异步任务方案

**问题**：项目当前未配置 Celery，但规范要求异步 embedding 生成和定时总结任务。

**决策**：引入 Celery + Redis Broker，配置 Beat 调度器

**理由**：
- 宪法技术栈明确列出 Celery 5.3+
- Redis 已作为项目基础设施存在（Docker），可直接用作 Broker
- Celery Beat 可满足定时总结任务需求（每日/每月）
- Celery 的重试机制（`max_retries`、`default_retry_delay`）天然适配 embedding 重试场景

**替代方案**：
- Django 后台线程 + APScheduler：非生产级，无法横向扩展，排除
- asyncio 原生任务：无持久化，进程重启丢失任务，排除
- django-huey：社区较小，宪法未提及，排除

**实施要点**：
1. 添加依赖：`celery>=5.3.0`、`django-celery-beat>=2.5.0`
2. 配置 Celery app：`backend/core/celery.py`
3. Redis Broker URL：`redis://localhost:6379/0`（与缓存共用 DB0，或使用 DB2 隔离）
4. 配置 Beat schedule：
   - `retry-failed-embeddings`：每 5 分钟
   - `daily-summary`：每天 00:00
   - `monthly-summary`：每月 1 日 00:00
5. Worker 启动命令：`celery -A core worker -l info`
6. Beat 启动命令：`celery -A core beat -l info`

---

## RES-004：Embedding API 调用方案

**问题**：需要确定 OpenAI API 兼容 Embedding 服务的调用方式。

**决策**：使用 `openai` Python SDK（已通过 `langchain-openai` 间接依赖），直接调用 `client.embeddings.create()`

**理由**：
- 项目已依赖 `langchain-openai`，间接包含 `openai` SDK
- OpenAI SDK 原生支持 API 兼容接口（通过 `base_url` 参数）
- 从 `ModelConfig` 表获取 `type='embedding'` 的配置（url, api_key, name, embedding_dimensions）
- API Key 需通过 SM4 解密后使用

**替代方案**：
- LangChain Embeddings 封装：增加中间层，规范要求"仅 OpenAI API 兼容接口"，排除
- httpx 直接调用：不如 SDK 类型安全，排除

**实施要点**：
1. 使用 `openai.AsyncOpenAI(api_key=decrypted_key, base_url=model_url)` 创建客户端
2. 调用 `client.embeddings.create(model=model_name, input=text)` 生成向量
3. 异常处理遵循宪法 4.3 LLM 异常处理规范
4. 通过 Langfuse 追踪 embedding 调用

---

## RES-005：Redis 分布式锁方案

**问题**：Safeguard 压缩需要 Redis 分布式锁避免同一用户并发压缩。

**决策**：使用 `redis-py` 内置的 `Lock` 实现

**理由**：
- `redis-py` 5.0+ 已内置分布式锁实现（基于 `SET NX EX` + Lua 脚本释放）
- 项目已依赖 `redis>=5.0.0`，无需额外引入
- 支持超时自动释放（防止死锁）
- 支持阻塞等待和非阻塞尝试

**替代方案**：
- Redlock（多 Redis 实例）：项目单实例部署，过度设计，排除
- 数据库行锁（SELECT FOR UPDATE）：不适合异步场景，排除

**实施要点**：
1. 锁 key：`compress:{user_id}`
2. 锁超时：60 秒（压缩 LLM 调用 + 重试的最大预估时长）
3. 阻塞等待：`lock.acquire(blocking=True, blocking_timeout=70)`
4. 获锁后重新检查 token 是否仍超限

---

## RES-006：新 Django App 结构决策

**问题**：记忆管理功能应放在哪个 Django app 中？

**决策**：创建新的 `apps/memory/` app

**理由**：
- 记忆管理是独立的业务域，有自己的数据模型（2 个新表）、服务层、仓库层
- 与 `chat` app 松耦合（chat 调用 memory 的服务，但不直接访问 memory 的数据）
- 符合宪法 1.1 关注点分离原则
- 上下文管理（ContextService）逻辑上属于 chat 与 memory 之间的编排层，放在 `chat/services.py` 中作为新的服务类

**替代方案**：
- 放在 `chat` app 中：职责过重，违反单一职责原则，排除
- 创建 `context` + `memory` 两个 app：过度拆分，增加复杂度，排除

**结构**：
```
backend/apps/memory/
├── __init__.py
├── models.py          # UserMemory, UserMemoryEmbedding
├── serializers.py     # 记忆 CRUD 序列化器
├── views.py           # 记忆 CRUD API 视图
├── services.py        # MemoryService（CRUD、搜索、总结）
├── repositories.py    # MemoryRepository, EmbeddingRepository
├── tasks.py           # Celery 异步任务（embedding 生成、定时总结）
└── urls.py            # URL 路由
```

上下文管理新增到 `chat/services.py`：
- `ContextService` 类：上下文窗口计算、裁剪、压缩编排

---

## RES-007：pgvector 向量维度动态方案

**问题**：embedding 向量维度如何管理。

**决策**：向量维度固定 2048，`VectorField(dimensions=2048)` 硬编码

**理由**：
- 规范明确要求维度固定 2048，简化实现，避免动态维度带来的 migration 和索引复杂度
- 写入时校验向量维度必须为 2048，否则报错拒绝

**替代方案**：
- 无维度声明 + 运行时校验：灵活但增加不必要复杂度，本期排除
- JSONB 存储向量：无法使用 pgvector 索引和距离函数，排除

**实施要点**：
1. `VectorField(dimensions=2048)` 固定维度声明
2. 写入前校验：`len(embedding_vector) == 2048`，不等则抛出异常
3. 索引创建延后：数据量增长后按需添加 HNSW 索引

---

## RES-008：关键词匹配降级方案

**问题**：`embedding_status != 'done'` 的记忆需退化为关键词匹配。

**决策**：使用 PostgreSQL 全文搜索（`tsvector` + `to_tsquery`），通过 Django ORM `SearchVector`/`SearchQuery`

**理由**：
- PostgreSQL 内置全文搜索，无需额外依赖
- Django 原生支持 `django.contrib.postgres.search`
- 与向量搜索结果合并后统一排序

**替代方案**：
- Python 端 `in` 操作 + 字符串匹配：无法利用索引，性能差，排除
- 引入 Elasticsearch：本期不使用 ES（宪法标记为可选），排除

**实施要点**：
1. `user_memory.content` 添加 GIN 索引用于全文搜索
2. 使用 `SearchVector('content', config='simple')` 支持中文分词
3. 语义搜索结果与关键词匹配结果合并、去重、按相关度排序

---

*文档版本：v1.0*
*创建日期：2026-01-30*
