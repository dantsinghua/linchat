# Memory 模块开发指南

> 用户长期记忆管理模块，提供记忆 CRUD、Embedding 向量生成、混合语义搜索、自动召回、定时总结等能力。

---

## 核心逻辑

### 记忆生命周期

```
用户/Agent 创建记忆
    → 写入 user_memory 表 (embedding_status=pending)
    → 投递 Celery 异步任务 generate_embedding
    → Embedding 生成成功 → 写入 user_memory_embedding 表 (status=done)
    → Embedding 生成失败 → retry_count++ (最多 3 次)
    → 记忆可被搜索（向量+关键词混合）
```

### 混合搜索机制

```
final_score = vector_score × 0.7 + keyword_score × 0.3
```

- **向量搜索**: pgvector CosineDistance，仅搜索 `embedding_status='done'` 的记忆
- **关键词搜索**: PostgreSQL 全文检索 (jiebacfg 中文分词)，搜索所有记忆
- **降级策略**: Embedding 不可用时自动降级为纯关键词搜索

### 定时总结

| 任务 | 调度 | 数据源优先级 |
|------|------|-------------|
| 每日总结 | 每天 00:00 | compaction 记忆 → message 表 → 跳过 |
| 每月总结 | 每月 1 日 00:00 | daily-summary → message 表 → 跳过 |

总结流程: 获取数据 → 调用 LLM 事实抽取(重试3次) → JSON 解析 → 存储为对应类型记忆

### 用户隔离 [R-004]

**所有操作强制按 `user_id` 隔离**，三层均有保障：

- Repository 层: 所有查询 WHERE 条件必带 `user_id`
- Service 层: 跨用户访问抛出 `MemoryNotFoundError`
- View 层: 跨用户访问返回 404（隐藏资源存在性）

---

## 项目结构

```
backend/apps/memory/
├── __init__.py           # 模块初始化
├── apps.py               # Django 应用配置 (MemoryConfig)
├── models.py             # 数据模型定义
├── repositories.py       # 数据访问层 (ORM 封装)
├── services.py           # 业务逻辑层 (核心)
├── views.py              # REST API 视图层
├── serializers.py        # 请求/响应序列化器
├── urls.py               # API 路由配置
├── tasks.py              # Celery 异步任务
# 注意: tools.py 已迁移到 apps/graph/tools/memory.py
├── migrations/
│   ├── __init__.py
│   └── 0001_initial.py   # 初始迁移 (pgvector + jiebacfg)
├── README.md             # 模块说明
└── CLAUDE.md             # 本文件
```

对应测试目录:

```
backend/tests/memory/
├── __init__.py
├── test_models.py        # 模型层测试
├── test_repositories.py  # 数据访问层测试
├── test_services.py      # 业务逻辑层测试
├── test_views.py         # API 端点测试
├── test_tasks.py         # Celery 任务测试
├── test_tools.py         # Agent 工具集测试
├── test_isolation.py     # 用户隔离专项测试
└── test_performance.py   # 性能基准测试
```

---

## 文件描述

### models.py — 数据模型

定义两个核心模型：

**UserMemory** (元数据表)
- `user_id`: 用户隔离键，所有查询必须携带
- `type`: 记忆类型枚举 — `memory` / `compaction` / `daily-summary` / `monthly-summary`
- `embedding_status`: 向量状态枚举 — `pending` / `processing` / `done` / `failed`
- `retry_count`: Embedding 生成重试计数，上限 3 次
- `tags`: JSON 字段，存储标签数组
- `importance_score`: 重要性评分（浮点数）

**UserMemoryEmbedding** (向量表)
- `memory`: 外键关联 UserMemory，CASCADE 级联删除
- `user_id`: 冗余字段，用于向量搜索时直接过滤
- `chunk_index` / `chunk_text`: 分块索引和文本
- `embedding`: pgvector VectorField，2048 维

### repositories.py — 数据访问层

**MemoryRepository**: 记忆元数据的 CRUD、分页查询、重试扫描、超时检测、按类型/时间范围查询、活跃用户查找

**EmbeddingRepository**: 向量记录的 CRUD、向量相似度搜索 (CosineDistance)、关键词全文搜索 (SearchVector + SearchQuery + jiebacfg)

所有方法均为异步 (`@sync_to_async` 包装 Django ORM)，强制要求 `user_id` 参数。

### services.py — 业务逻辑层（核心文件）

三个组件：

**EmbeddingClient** (静态方法类)
- `generate_embedding(text)`: 调用 OpenAI 兼容 API 生成 2048 维向量
- 从 `ModelConfig` 读取 `type='embedding'` 的模型配置
- Token 超限时使用 tiktoken 截断至 8192 tokens

**MemoryService** (主业务类)
- `create_memory()`: 创建记忆 + 投递 Embedding 异步任务
- `update_memory()`: 更新内容 + 重置 Embedding 状态为 pending
- `delete_memory()`: 删除记忆（级联删除向量）
- `get_memory()` / `list_memories()`: 查询
- `search_memory()`: 混合搜索（向量 0.7 + 关键词 0.3），支持降级
- `summarize_and_store()`: LLM 事实抽取 + 存储总结记忆
- `retrieve_relevant_memories()`: 自动召回并格式化记忆上下文

### views.py — REST API 视图层

使用 `@api_view` 装饰器的函数视图，通过 `async_to_sync()` 调用异步服务方法。

| 视图函数 | 方法 | 路径 |
|---------|------|------|
| `memory_list_create` | GET/POST | `/api/v1/memories/` |
| `memory_detail` | GET/PUT/DELETE | `/api/v1/memories/<id>/` |
| `memory_search` | POST | `/api/v1/memories/search/` |

### serializers.py — 序列化器

- `MemoryCreateSerializer`: 创建请求 (content 必填, max_length=10000)
- `MemoryUpdateSerializer`: 更新请求 (content 必填)
- `MemoryResponseSerializer`: 标准响应
- `MemorySearchSerializer`: 搜索请求 (query 必填, limit 1-20 默认 5)
- `MemorySearchResultSerializer`: 搜索结果 (含 score + match_type)
- `MemoryListQuerySerializer`: 列表查询参数 (type/page/page_size)

### tools.py — 已迁移到 apps/graph/tools/memory.py

LangGraph Agent 记忆工具集已迁移到 `apps/graph/tools/memory.py`。
导入方式: `from apps.graph.tools.memory import MEMORY_TOOLS`

### tasks.py — Celery 异步任务

| 任务 | 触发方式 | 功能 |
|------|---------|------|
| `generate_embedding` | 记忆创建/更新时投递 | 生成 Embedding，状态流转 pending→processing→done/failed |
| `retry_failed_embeddings` | Beat 每 5 分钟 | 扫描 failed(retry<3) 和超时 pending 记录，重新投递 |
| `generate_daily_summary` | Beat 每天 00:00 | 前一天活跃用户的记忆日总结 |
| `generate_monthly_summary` | Beat 每月 1 日 00:00 | 上月活跃用户的记忆月总结 |

### migrations/0001_initial.py — 初始迁移

执行四项关键操作：
1. 启用 `pgvector` 扩展
2. 尝试创建 `pg_jieba` 扩展（失败时降级为 `simple` 分词器）
3. 创建 `jiebacfg` 文本搜索配置
4. 创建 GIN 索引: `idx_um_content_tsv` 用于全文检索

---

## 技术规范

### 分层架构（严格遵守）

```
views.py (视图层)       → 仅处理 HTTP 请求响应，禁止业务逻辑
services.py (服务层)    → 封装所有业务逻辑 ★ 核心
repositories.py (数据层) → 封装 ORM/向量搜索操作，强制 user_id 隔离
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
| `MEMORY_EMBEDDING_DIMENSION` | 2048 | Embedding 向量维度 |
| `MEMORY_SEARCH_TOP_K` | 5 | 搜索默认返回条数 |
| `MEMORY_VECTOR_WEIGHT` | 0.7 | 混合搜索向量权重 |
| `MEMORY_KEYWORD_WEIGHT` | 0.3 | 混合搜索关键词权重 |
| `MEMORY_EMBEDDING_MAX_RETRY` | 3 | Embedding 最大重试次数 |
| `COMPRESS_LOCK_TIMEOUT` | 60s | 上下文压缩分布式锁超时 |

### 数据库依赖

- **PostgreSQL 15** + `pgvector` 扩展（向量存储与相似度搜索）
- **pg_jieba** 扩展（中文分词，不可用时降级为 `simple`）
- GIN 索引加速全文检索

### 外部依赖

- **OpenAI 兼容 Embedding API**: 通过 `ModelConfig` 表动态获取配置
- **tiktoken**: Token 计数与截断
- **Celery + Redis**: 异步任务队列 (Broker: Redis DB2)
- **Langfuse**: LLM 调用追踪

---

## 实现功能清单

### 记忆 CRUD
- [x] 创建记忆（自动投递 Embedding 任务）
- [x] 更新记忆（重置 Embedding 状态，重新生成向量）
- [x] 删除记忆（级联删除向量数据）
- [x] 查询单条记忆详情
- [x] 分页列表查询（支持 type 过滤）

### Embedding 管理
- [x] 异步生成 2048 维向量
- [x] 状态机流转: pending → processing → done / failed
- [x] 失败自动重试（最多 3 次）
- [x] 超时检测与恢复（pending 超过 300s）
- [x] 定时扫描任务（每 5 分钟）

### 混合搜索
- [x] 向量语义搜索 (pgvector CosineDistance)
- [x] 关键词全文搜索 (PostgreSQL jiebacfg)
- [x] 加权融合 (0.7 / 0.3)
- [x] Embedding 不可用时降级为纯关键词搜索

### 定时总结
- [x] 每日总结（compaction → message → 跳过）
- [x] 每月总结（daily-summary → message → 跳过）
- [x] LLM 事实抽取 + JSON 解析（失败回退为原始文本）
- [x] 单用户失败不影响其他用户

### Agent 集成
- [x] `mem_search` / `mem_cache` / `mem_update` / `mem_delete` 四个 LangGraph 工具
- [x] `retrieve_relevant_memories()` 自动召回接口

---

## 测试指导

### 运行测试

```bash
# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行全部 memory 测试
pytest tests/memory/ -v

# 运行单个测试文件
pytest tests/memory/test_services.py -v

# 运行单个测试类
pytest tests/memory/test_services.py::TestMemoryServiceCreate -v

# 带覆盖率报告
pytest tests/memory/ --cov=apps.memory --cov-report=term-missing
```

### 测试矩阵

| 测试文件 | 测试对象 | 用例数 | 关注点 |
|---------|---------|-------|-------|
| `test_models.py` | ORM 模型 | ~6 | 默认值、枚举、级联删除 |
| `test_repositories.py` | 数据访问层 | ~12 | CRUD、用户隔离、分页、搜索骨架 |
| `test_services.py` | 业务逻辑层 | ~20 | 混合搜索、LLM 重试、降级策略、Celery 容错 |
| `test_views.py` | API 端点 | ~20 | HTTP 状态码、参数验证、响应格式、分页 |
| `test_tasks.py` | Celery 任务 | ~18 | 状态流转、重试扫描、每日/月总结 |
| `test_tools.py` | Agent 工具集 | ~12 | 工具注册、user_id 传播、返回格式 |
| `test_isolation.py` | 用户隔离 | ~10 | 三层隔离验证、404 隐藏存在性、数据不变性 |
| `test_performance.py` | 性能基准 | ~8 | 分词延迟、裁剪延迟、组装延迟 |

**总计约 106 个测试用例，预估覆盖率 ~88%**

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
    mock_embed.return_value = [0.1] * 2048
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

**数据不变性验证**: 失败操作后必须验证数据未被篡改

```python
memory.refresh_from_db()
assert memory.content == "原始内容"  # 确保操作失败后无副作用
```

### 新增功能测试要求

- 新增 Service 方法 → 必须在 `test_services.py` 添加对应测试
- 新增 API 端点 → 必须在 `test_views.py` 添加成功/失败/参数校验测试
- 涉及用户数据 → 必须在 `test_isolation.py` 添加隔离测试
- 涉及 Celery 任务 → 必须在 `test_tasks.py` 测试状态流转和异常处理
- Service 层覆盖率目标 ≥ 95%，总体覆盖率 ≥ 80%
