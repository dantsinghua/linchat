# Memory 模块开发指南

> 用户长期记忆管理模块，提供记忆 CRUD、Embedding 向量生成、混合语义搜索、自动召回、定时总结等能力。

---

## 核心逻辑

### 记忆生命周期

```
用户/Agent 创建记忆
    -> 写入 user_memory 表 (embedding_status=pending)
    -> 投递 Celery 异步任务 generate_embedding
    -> 活跃用户检查（Redis auth:token:* 键扫描）
        -> 有活跃用户 -> 跳过执行，保持 pending（下次重试扫描重新投递）
        -> 无活跃用户 -> 继续执行
    -> Embedding 生成成功 -> 写入 user_memory_embedding 表 (status=done)
    -> Embedding 生成成功后 -> 预热语言模型（强制 vLLM 加载语言模型回 GPU）
    -> Embedding 生成失败 -> retry_count++ (最多 3 次)
    -> 记忆可被搜索（向量+关键词混合）
```

### GPU 互斥机制

单 GPU 环境下，Embedding 模型和语言模型共享 GPU 资源。tasks.py 实现了以下互斥策略：

1. **活跃用户检查** (`_has_active_users()`): 通过 Redis 扫描 `auth:token:*` 键判断是否有在线用户（Token TTL=3600s）。有用户在线时跳过 embedding 任务，避免 GPU 模型热切换
2. **语言模型预热** (`_warmup_language_model()`): embedding 生成成功后，向语言模型 API 发送最小请求（max_tokens=1），强制 vLLM 将语言模型加载回 GPU
3. **搜索路径跳过向量**: `search_memory()` 的 `skip_vector=True` 参数允许聊天路径仅使用关键词搜索，避免触发 embedding API

### 混合搜索机制

```
final_score = vector_score x 0.7 + keyword_score x 0.3
```

- **向量搜索**: pgvector CosineDistance，仅搜索 `embedding_status='done'` 的记忆
- **关键词搜索**: PostgreSQL 全文检索 (jiebacfg 中文分词)，搜索所有记忆
- **降级策略**: Embedding 不可用时（skip_vector=True 或 API 异常）自动降级为纯关键词搜索

### 定时总结

| 任务 | 调度 | 数据源优先级 |
|------|------|-------------|
| 每日总结 | 每天 00:00 | compaction 记忆 -> message 表 -> 跳过 |
| 每月总结 | 每月 1 日 00:00 | daily-summary -> message 表 -> 跳过 |

总结流程: 获取数据 -> 调用 LLM 事实抽取(重试3次) -> JSON 解析 -> 存储为对应类型记忆

### 用户隔离 [R-004]

**所有操作强制按 `user_id` 隔离**，三层均有保障：

- Repository 层: 所有查询 WHERE 条件必带 `user_id`，缺失则抛出 `ValueError`
- Service 层: 跨用户访问抛出 `MemoryNotFoundError`
- View 层: 跨用户访问返回 404（隐藏资源存在性）

---

## 项目结构

```
backend/apps/memory/
├── __init__.py           # 模块初始化
├── apps.py               # Django 应用配置 (MemoryConfig)
├── models.py             # 数据模型定义（UserMemory + UserMemoryEmbedding）
├── repositories.py       # 数据访问层 (ORM + pgvector + 全文检索封装)
├── services.py           # 业务逻辑层 (核心: EmbeddingClient + MemoryService)
├── views.py              # REST API 视图层（函数视图）
├── serializers.py        # 请求/响应序列化器
├── urls.py               # API 路由配置
├── tasks.py              # Celery 异步任务（embedding 生成 + 重试 + 定时总结 + 健康检查）
├── migrations/
│   ├── __init__.py
│   ├── 0001_initial.py               # 初始迁移 (pgvector + jiebacfg + 建表 + GIN索引)
│   └── 0002_change_embedding_dim_to_1024.py  # 向量维度改为 1024 + 移除 verbose_name
└── CLAUDE.md             # 本文件
```

注意: Agent 记忆工具集已迁移到 `apps/graph/tools/memory.py`

---

## 文件描述

### models.py -- 数据模型

定义两个核心模型：

**UserMemory** (元数据表，db_table=`user_memory`)
- `id`: BigAutoField 主键
- `user_id`: BigIntegerField，用户隔离键（db_index），所有查询必须携带
- `type`: CharField，记忆类型枚举 -- `memory` / `compaction` / `daily-summary` / `monthly-summary`
- `content`: TextField，记忆文本
- `embedding_status`: CharField，向量状态枚举 -- `pending` / `processing` / `done` / `failed`
- `retry_count`: IntegerField，Embedding 生成重试计数，上限 3 次
- `name`: CharField(200)，可选名称
- `tags`: JSONField，存储标签数组
- `importance_score`: FloatField，重要性评分
- `created_at` / `updated_at`: 自动时间戳

数据库索引：`idx_um_embedding_status` / `idx_um_user_type` / `idx_um_status_retry` / `idx_um_user_created`

**UserMemoryEmbedding** (向量表，db_table=`user_memory_embedding`)
- `id`: BigAutoField 主键
- `memory`: ForeignKey -> UserMemory，CASCADE 级联删除
- `user_id`: BigIntegerField，冗余字段（加速向量搜索直接过滤）
- `type` / `name`: 冗余字段
- `chunk_index`: IntegerField，分块序号
- `chunk_text`: TextField，分块文本
- `embedding`: pgvector VectorField，**1024 维**
- `created_at`: 自动时间戳

数据库索引：`idx_ume_memory` / GIN 索引 `idx_um_content_tsv`（全文检索）

### repositories.py -- 数据访问层

**MemoryRepository** (全局实例: `memory_repo`)

| 方法 | 说明 |
|------|------|
| `create(memory)` | 保存记忆 |
| `get_by_id(memory_id, user_id)` | 按 ID+user_id 查询 |
| `get_by_user_id(user_id)` | 查询用户全部记忆 |
| `batch_get_by_ids(ids, user_id)` | 批量查询（返回 {id: memory} 字典） |
| `update(memory)` | 更新记忆 |
| `delete(memory_id, user_id)` | 删除记忆 |
| `list_by_user(user_id, type_filter, page, page_size)` | 分页查询 |
| `find_retryable(max_retry)` | 查找可重试的 failed 记录 |
| `find_pending_timeout(timeout_seconds)` | 查找超时的 pending 记录 |
| `find_by_type_and_date_range(user_id, type, start, end)` | 按类型+时间范围查询 |
| `find_active_users_for_daily(target_date)` | 查找某日活跃用户 ID 列表 |
| `find_active_users_for_monthly(year, month)` | 查找某月活跃用户 ID 列表 |

**EmbeddingRepository** (全局实例: `embedding_repo`)

| 方法 | 说明 |
|------|------|
| `create(embedding)` | 保存向量记录 |
| `delete_by_memory_id(memory_id)` | 按 memory_id 删除向量 |
| `get_by_memory_id(memory_id, user_id)` | 查询向量记录 |
| `vector_search(user_id, query_embedding, limit)` | pgvector 余弦相似度搜索，返回 `[(memory_id, score)]` |
| `keyword_search(user_id, query_text, limit)` | PostgreSQL jiebacfg 全文检索，返回 `[(memory_id, rank)]` |

所有方法均为异步（`@sync_to_async` 包装 Django ORM）。

### services.py -- 业务逻辑层（核心文件）

**自定义异常**:
- `EmbeddingConfigNotFoundError`: 未配置 Embedding 模型
- `MemoryNotFoundError`: 记忆不存在
- `MemoryPermissionError`: 无权访问

**EmbeddingClient** (静态方法类)
- `_get_embedding_config()`: 从 `model_service.get_active_model("embedding")` 获取配置
- `generate_embedding(text)`: 调用 OpenAI 兼容 API 生成 1024 维向量（维度由 `settings.MEMORY_EMBEDDING_DIMENSION` 控制），超限时 tiktoken 截断至 max_input_tokens

**MemoryService** (主业务类)

| 方法 | 说明 |
|------|------|
| `create_memory(user_id, content, name, type, tag)` | 创建记忆 + 投递 Embedding 异步任务 |
| `update_memory(memory_id, user_id, content, tag)` | 更新内容 + 重置 Embedding 为 pending |
| `delete_memory(memory_id, user_id)` | 删除记忆（级联删除向量） |
| `get_memory(memory_id, user_id)` | 查询单条 |
| `list_memories(user_id, type_filter, page, page_size)` | 分页查询 |
| `search_memory(user_id, query, limit, skip_vector)` | 混合搜索（skip_vector=True 时仅关键词） |
| `summarize_and_store(user_id, content, summary_type, summary_name)` | LLM 事实抽取 + 存储 |
| `retrieve_relevant_memories(user_id, query, limit)` | 自动召回并格式化为上下文字符串 |

### views.py -- REST API 视图层

使用 `@api_view` 装饰器的函数视图，通过 `async_to_sync()` 调用异步服务方法。

| 视图函数 | 方法 | 路径 |
|---------|------|------|
| `memory_list_create` | GET/POST | `/api/v1/memories/` |
| `memory_detail` | GET/PUT/DELETE | `/api/v1/memories/<id>/` |
| `memory_search` | POST | `/api/v1/memories/search/` |

### serializers.py -- 序列化器

| 序列化器 | 说明 |
|---------|------|
| `MemoryCreateSerializer` | 创建请求（content 必填，max_length=MEMORY_CONTENT_MAX_LENGTH；name 可选） |
| `MemoryUpdateSerializer` | 更新请求（content 必填） |
| `MemoryResponseSerializer` | ModelSerializer 标准响应（id/type/name/content/embedding_status/tags/created_at/updated_at） |
| `MemorySearchSerializer` | 搜索请求（query 必填；limit 1-20 默认 5） |
| `MemorySearchResultSerializer` | 搜索结果（继承 Response + score + match_type） |
| `MemoryListQuerySerializer` | 列表查询参数（type 可选过滤；page/page_size 分页） |

### tasks.py -- Celery 异步任务

| 任务名 | Celery 名称 | 触发方式 | 功能 |
|--------|------------|---------|------|
| `generate_embedding` | `memory.generate_embedding` | 记忆创建/更新时投递 | 生成 Embedding，活跃用户检查 -> pending->processing->done/failed -> 语言模型预热 |
| `retry_failed_embeddings` | `memory.retry_failed_embeddings` | Beat 定时 | 扫描 failed(retry<3) 和超时 pending 记录，活跃用户检查后重新投递 |
| `generate_daily_summary` | `memory.generate_daily_summary` | Beat 每天 00:00 | 前一天活跃用户的记忆日总结（compaction -> message -> 跳过） |
| `generate_monthly_summary` | `memory.generate_monthly_summary` | Beat 每月 1 日 00:00 | 上月活跃用户的记忆月总结（daily-summary -> message -> 跳过） |
| `embedding_health_check` | `memory.embedding_health_check` | Beat 每小时 | 重置可重试 failed 记录 + 标记超时 pending(1h)/processing(10min) 为 failed + 失败数告警 |

辅助函数：
- `_has_active_users()`: Redis auth:token:* 键扫描，判断是否有活跃用户
- `_warmup_language_model()`: 向工具模型发送最小请求，强制 vLLM 加载语言模型回 GPU
- `_run_summary()`: 定时总结公共逻辑（查找活跃用户 -> 收集内容(primary -> message fallback) -> summarize）

### migrations

| 迁移文件 | 说明 |
|---------|------|
| `0001_initial.py` | 启用 pgvector 扩展 + pg_jieba/jiebacfg 搜索配置（降级 simple）+ 建表 UserMemory/UserMemoryEmbedding + 索引 + GIN 全文检索索引 |
| `0002_change_embedding_dim_to_1024.py` | 向量维度从初始值改为 1024 + 移除部分字段 verbose_name |

---

## 技术规范

### 分层架构（严格遵守）

```
views.py (视图层)       -> 仅处理 HTTP 请求响应，禁止业务逻辑
services.py (服务层)    -> 封装所有业务逻辑（核心层）
repositories.py (数据层) -> 封装 ORM/向量搜索操作，强制 user_id 隔离
```

### 异步模式

- Service / Repository 方法均为 `async`
- Repository 使用 `@sync_to_async` 包装 Django ORM
- View 层使用 `async_to_sync()` 调用异步服务
- Celery 任务中使用 `asyncio.new_event_loop()` 运行异步代码

### 配置常量

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `MEMORY_EMBEDDING_PENDING_TIMEOUT` | 300s | pending 状态超时阈值 |
| `MEMORY_CONTENT_MAX_LENGTH` | 10000 | 记忆内容最大字符数 |
| `MEMORY_EMBEDDING_DIMENSION` | 1024 | Embedding 向量维度 |
| `MEMORY_SEARCH_TOP_K` | 5 | 搜索默认返回条数 |
| `MEMORY_VECTOR_WEIGHT` | 0.7 | 混合搜索向量权重 |
| `MEMORY_KEYWORD_WEIGHT` | 0.3 | 混合搜索关键词权重 |
| `MEMORY_EMBEDDING_MAX_RETRY` | 3 | Embedding 最大重试次数 |
| `COMPRESS_LOCK_TIMEOUT` | 60s | 上下文压缩分布式锁超时 |

### 数据库依赖

- **PostgreSQL 15** + `pgvector` 扩展（向量存储与余弦相似度搜索）
- **pg_jieba** 扩展（中文分词，不可用时降级为 `simple`）
- GIN 索引 `idx_um_content_tsv` 加速全文检索

### 外部依赖

| 依赖 | 说明 |
|------|------|
| OpenAI 兼容 Embedding API | 通过 `ModelConfig` 表动态获取 `type='embedding'` 的配置 |
| tiktoken | Token 计数与截断 |
| Celery + Redis | 异步任务队列 (Broker: Redis DB2) |
| apps.models.services | `model_service.get_active_model("embedding"/"tool")` |
| apps.graph.agent | `get_llm()` 用于事实抽取 |
| apps.graph.prompts | `CRONMEM_PROMPT_TEMPLATE` 用于事实抽取 Prompt |
| apps.chat.models | `Message` 模型用于定时总结数据源降级 |

---

## 测试指导

### 运行测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行全部 memory 测试
pytest tests/memory/ -v

# 运行单个测试文件
pytest tests/memory/test_services.py -v

# 带覆盖率报告
pytest tests/memory/ --cov=apps.memory --cov-report=term-missing
```

### 测试模式与约定

**异步方法测试**: 使用 `tests/helpers.py` 中的 `run_async(coro)` 执行异步代码

```python
from tests.helpers import run_async

def test_example(self):
    result = run_async(MemoryService.search_memory(user_id=1, query="测试"))
```

**Mock 外部依赖**: Embedding API、LLM 调用、Celery 任务投递均需 mock

```python
@patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
def test_search(self, mock_embed: AsyncMock) -> None:
    mock_embed.return_value = [0.1] * 1024
```

**认证模拟**: View 层测试需 mock Token 中间件

```python
with patch(
    "apps.common.middleware.TokenAuthMiddleware._verify_token_sync",
    return_value={"user_id": 1, ...}
):
    response = self.client.get("/api/v1/memories/")
```

**隔离验证三原则**:
1. Repository 层: 跨用户查询返回 `None`
2. Service 层: 跨用户操作抛出 `MemoryNotFoundError`
3. View 层: 跨用户请求返回 404（不返回 403，隐藏资源存在性）

### 新增功能测试要求

- 新增 Service 方法 -> 必须在 `test_services.py` 添加对应测试
- 新增 API 端点 -> 必须在 `test_views.py` 添加成功/失败/参数校验测试
- 涉及用户数据 -> 必须添加隔离测试
- 涉及 Celery 任务 -> 必须测试状态流转和异常处理
- Service 层覆盖率目标 >= 95%，总体覆盖率 >= 80%
