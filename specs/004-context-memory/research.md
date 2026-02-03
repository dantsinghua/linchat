# 技术研究 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-31

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
4. 向量维度固定 2048，`VectorField(dimensions=2048)` 硬编码

---

## RES-002：tiktoken 集成方案

**问题**：项目当前未依赖 tiktoken，需确定集成方式。

**决策**：直接添加 `tiktoken` 依赖，使用 `cl100k_base` 编码

**理由**：
- tiktoken 是 OpenAI 官方 token 计数库，精度最高
- `cl100k_base` 编码覆盖 GPT-4/GPT-3.5-turbo 等主流模型
- 规范明确指定使用此方案（spec.md FR-002、clarification 2026-01-30）

**替代方案**：
- 字符估算（chars/4）：精度不够，可能导致上下文溢出，排除
- transformers tokenizer：依赖过重，排除

**实施要点**：
1. 添加依赖：`tiktoken>=0.7.0`
2. 创建工具模块 `apps/common/tokenizer.py`，封装 token 计数函数
3. 全局缓存编码器实例，避免重复初始化
4. 提供 `count_tokens(text: str) -> int` 和 `count_messages_tokens(messages: list[dict]) -> int` 两个公共方法

---

## RES-003：Celery 异步任务方案

**问题**：项目当前未配置 Celery，但规范要求异步 embedding 生成和定时总结任务。

**决策**：引入 Celery + Redis Broker (DB2)，配置 Beat 调度器

**理由**：
- 宪法技术栈明确列出 Celery 5.3+
- Redis 已作为项目基础设施存在（Docker），DB2 专用于 Celery Broker（CLAUDE.md 已标注 DB2: Celery Broker）
- Celery Beat 可满足定时总结任务需求（每日/每月）
- Celery 的重试机制（`max_retries`、`default_retry_delay`）天然适配 embedding 重试场景

**替代方案**：
- Django 后台线程 + APScheduler：非生产级，无法横向扩展，排除
- asyncio 原生任务：无持久化，进程重启丢失任务，排除
- django-huey：社区较小，宪法未提及，排除

**实施要点**：
1. 添加依赖：`celery>=5.3.0`、`django-celery-beat>=2.5.0`
2. 配置 Celery app：`backend/core/celery.py`
3. Redis Broker URL：`redis://localhost:6379/2`（DB2 专用）
4. 配置 Beat schedule：
   - `retry-failed-embeddings`：每 5 分钟
   - `daily-summary`：每天 00:00
   - `monthly-summary`：每月 1 日 00:00
5. Worker 启动命令：`celery -A core worker -l info`
6. Beat 启动命令：`celery -A core beat -l info`
7. `core/__init__.py` 中加载 Celery app：`from .celery import app as celery_app`

---

## RES-004：Embedding API 调用方案

**问题**：需要确定 OpenAI API 兼容 Embedding 服务的调用方式。

**决策**：使用 `openai` Python SDK（已通过 `langchain-openai` 间接依赖），直接调用 `client.embeddings.create()`

**理由**：
- 项目已依赖 `langchain-openai`，间接包含 `openai` SDK
- OpenAI SDK 原生支持 API 兼容接口（通过 `base_url` 参数）
- 从 `ModelConfig` 表获取 `type='embedding'` 的配置（url, api_key, name）
- API Key 需通过 SM4 解密后使用

**替代方案**：
- LangChain Embeddings 封装：增加中间层，规范要求"仅 OpenAI API 兼容接口"，排除
- httpx 直接调用：不如 SDK 类型安全，排除

**实施要点**：
1. 使用 `openai.AsyncOpenAI(api_key=decrypted_key, base_url=model_url)` 创建客户端
2. 调用 `client.embeddings.create(model=model_name, input=text)` 生成向量
3. 校验返回向量维度 = 2048，否则抛出 `ValueError`
4. content token 超出模型输入限制时截取前 N tokens
5. 异常处理遵循宪法 4.3 LLM 异常处理规范
6. 通过 Langfuse 追踪 embedding 调用

---

## RES-005：Redis 分布式锁方案

**问题**：上下文压缩需要 Redis 分布式锁避免同一用户并发压缩。

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
4. 获锁后重新检查 token 是否仍超限（可能前一个请求已完成压缩）
5. 使用 `core/redis.py` 中已有的异步 Redis 客户端

---

## RES-006：新 Django App 结构决策

**问题**：记忆管理功能应放在哪个 Django app 中？

**决策**：创建新的 `apps/memory/` app

**理由**：
- 记忆管理是独立的业务域，有自己的数据模型（2 个新表）、服务层、仓库层
- 与 `chat` app 松耦合（chat 调用 memory 的服务，但不直接访问 memory 的数据）
- 符合宪法 1.1 关注点分离原则
- 上下文管理（ContextService）作为编排层放在 `chat/services/context_service.py`，因为它协调 chat 和 memory 两个域

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
├── tools.py           # LangGraph 记忆工具集
└── urls.py            # URL 路由
```

上下文管理新增到 `chat/services/`：
- `context_service.py` — ContextService 类：上下文窗口计算、裁剪、压缩编排

上下文工具新增到 `chat/`：
- `tools.py` — 上下文工具集（contextCompact/contextExtract/contextPrune）

---

## RES-007：pgvector 向量维度方案

**问题**：embedding 向量维度如何管理。

**决策**：向量维度固定 2048，`VectorField(dimensions=2048)` 硬编码

**理由**：
- 规范明确要求维度固定 2048（spec.md 澄清 2026-01-30），简化实现
- model 表中 `embedding_dimensions` 字段仅作参考记录，实际写入时硬编码 2048 并校验
- 避免动态维度带来的 migration 和索引复杂度

**替代方案**：
- 无维度声明 + 运行时校验：灵活但增加不必要复杂度，本期排除
- JSONB 存储向量：无法使用 pgvector 索引和距离函数，排除

**实施要点**：
1. `VectorField(dimensions=2048)` 固定维度声明
2. 写入前校验：`len(embedding_vector) == 2048`，不等则抛出异常
3. 向量索引（HNSW/IVFFlat）在数据量增长后按需添加

---

## RES-008：关键词匹配降级方案

**问题**：`embedding_status != 'done'` 的记忆需退化为关键词匹配，如何实现？

**决策**：使用 PostgreSQL 全文搜索（tsvector + GIN 索引），必须安装 pg_jieba 中文分词插件

**理由**：
- PostgreSQL 内置全文搜索，无需额外外部依赖
- pg_jieba 提供中文分词能力，`to_tsvector('jiebacfg', content)` 直接支持中文
- Django `django.contrib.postgres.search` 模块支持 ORM 级别的全文检索
- 与向量搜索结果合并后统一排序（向量 0.7 + 关键词 0.3 加权）

**替代方案**：
- Python 端 `in` 操作 + 字符串匹配：无法利用索引，性能差，排除
- 引入 Elasticsearch：宪法标记为可选组件，本期不使用，排除
- zhparser 分词：pg_jieba 社区更活跃，分词精度更高，排除

**实施要点**：
1. Docker PostgreSQL 容器中安装 pg_jieba 扩展
2. `user_memory.content` 添加 GIN 索引：`CREATE INDEX idx_user_memory_content_tsv ON user_memory USING GIN (to_tsvector('jiebacfg', content))`
3. 搜索查询：`SearchVector('content', config='jiebacfg')` + `SearchQuery(query, config='jiebacfg')`
4. 混合检索：向量搜索结果（score * 0.7）+ 关键词搜索结果（score * 0.3），去重合并，按加权得分排序
5. TopK = 5，最多返回 5 条结果

---

## RES-009：pg_jieba 安装方案

**问题**：pg_jieba 中文分词插件需要在 PostgreSQL 容器中编译安装。

**决策**：自定义 Dockerfile 基于 `postgres:15-alpine` 构建带 pg_jieba 的镜像

**理由**：
- pg_jieba 不在官方 PostgreSQL 镜像中，需要从源码编译
- 使用自定义 Dockerfile 可以确保构建一致性和可复现性
- Alpine 基础镜像体积小，适合生产环境

**实施要点**：
1. 创建 `docker/postgres/Dockerfile`：
   ```dockerfile
   FROM postgres:15-alpine
   RUN apk add --no-cache git cmake make g++ postgresql15-dev
   RUN git clone https://github.com/jaiminpan/pg_jieba.git /tmp/pg_jieba \
       && cd /tmp/pg_jieba \
       && git submodule update --init --recursive \
       && mkdir build && cd build \
       && cmake .. && make && make install \
       && rm -rf /tmp/pg_jieba
   ```
2. 更新 `docker-compose.yml` 使用自定义镜像
3. 初始化脚本创建扩展：`CREATE EXTENSION IF NOT EXISTS pg_jieba;`
4. 配置分词方案名称：`jiebacfg`
5. **备选方案**：若 pg_jieba 编译失败，退化为 PostgreSQL 默认 `simple` 配置（无中文分词，按空格分词）

---

## RES-010：PromptBuilder 设计决策

**问题**：现有 `apps/chat/prompts.py` 已有 PromptBuilder 骨架，需确定扩展方案。

**决策**：在现有 PromptBuilder 基础上扩展，实现规范 FR-014 的全部方法

**理由**：
- 现有 `prompts.py` 文件已存在且有基础结构，无需从零创建
- PromptBuilder 职责明确：分层组装最终消息列表
- 功能模块注册机制（PromptModule 枚举 + PromptRegistry）支持运行时动态扩展

**现有代码调研**：
- `prompts.py` 已在 `apps/chat/` 下创建但为占位状态
- 现有 `agent.py` 使用简单字符串拼接 system prompt，需迁移到 PromptBuilder

**实施要点**：
1. 实现 6 个核心 build 方法：
   - `build_system_prompt(modules)` — 层级 1
   - `build_template_block()` — 层级 2.a
   - `build_memory_block(user_id, user_message)` — 层级 2.b
   - `build_tool_context(tools)` — 层级 2.c
   - `build_conversation_history(user_id, limit)` — 层级 2.d
   - `build_messages()` / `build_messages_for_langchain()` — 最终组装
2. 实现 PromptModule 枚举（BASE/REASONING/TOOL_USAGE/CODE_ASSIST/CREATIVE_WRITING/DATA_ANALYSIS）
3. 实现 `register_custom_module()` 运行时动态扩展
4. 每段加载后调用 tokenizer 计算 token 数，超限时触发压缩流程
5. 创建 4 个专用模板常量：COMPACTION/DAILY_SUMMARY/MONTHLY_SUMMARY/CRONMEM

---

## RES-011：mem0 Prompt 参考设计

> 已抽取为独立文档：[mem0-prompt-reference.md](mem0-prompt-reference.md)
>
> 包含：事实抽取 Prompt、记忆去重合并 Prompt、程序性记忆总结 Prompt 的原始设计及适配 LinChat 的改造方向。
> 作为 cronMem 流程 CRONMEM_PROMPT_TEMPLATE 的设计参考。

---

## RES-012：LangGraph 多流程工厂设计

**问题**：现有 `agent.py` 仅有 chat 流程（ReAct Agent），需新增 3 个流程。

**决策**：在 `agent.py` 中新增 3 个流程工厂函数，各流程使用独立的 StateGraph

**现有代码调研**：
- `agent.py` 已实现 `create_react_agent()` 工厂函数
- 使用 `langgraph` 的 `StateGraph` + `ToolNode` 模式
- 已集成 `langgraph-checkpoint-redis` 用于状态持久化
- Langfuse 追踪已就位

**实施要点**：
1. `create_context_agent(tools)` — context 流程，仅注册上下文工具
2. `create_memory_agent(tools)` — memory 流程，仅注册记忆工具
3. `create_cronmem_agent()` — cronMem 流程，无工具，仅 Agent → End
4. 各流程共享 Redis checkpoint 配置，但使用不同 `thread_id` 前缀
5. 各流程的工具集严格隔离，不可越界（→ rule-model R-018）

---

*文档版本：v2.0*
*创建日期：2026-01-30*
*更新日期：2026-01-31 — v2.0 新增 RES-009 pg_jieba 安装方案、RES-010 PromptBuilder 设计决策、RES-012 LangGraph 多流程工厂设计，更新 RES-003 Broker 为 Redis DB2，更新 RES-008 明确 pg_jieba 实现*
