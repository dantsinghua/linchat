# Memory 模块

用户长期记忆管理模块，提供记忆 CRUD、语义搜索、自动召回、定时总结等能力。

## 模块职责

- 用户记忆的创建、查询、更新、删除 (CRUD)
- Embedding 向量异步生成与管理
- 混合搜索（向量语义 + 关键词全文）
- 定时记忆总结（每日/每月）
- LangGraph Agent 工具集集成

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/memories/` | 分页获取记忆列表（支持 type 过滤） |
| POST | `/api/v1/memories/` | 创建新记忆 |
| GET | `/api/v1/memories/<id>/` | 获取单条记忆详情 |
| PUT | `/api/v1/memories/<id>/` | 更新记忆内容 |
| DELETE | `/api/v1/memories/<id>/` | 删除记忆 |
| POST | `/api/v1/memories/search/` | 混合搜索（向量 + 关键词） |

所有接口强制 `user_id` 隔离，从 Token 中间件自动注入。

## 数据模型

```
user_memory (元数据表)
  ├── id (PK)
  ├── user_id (隔离键, indexed)
  ├── type (memory/compaction/daily-summary/monthly-summary)
  ├── name, content
  ├── embedding_status (pending/processing/done/failed)
  ├── retry_count
  ├── tags (JSON), importance_score
  └── created_at, updated_at

user_memory_embedding (向量表)
  ├── id (PK)
  ├── memory_id (FK → user_memory, CASCADE)
  ├── user_id (冗余, indexed)
  ├── chunk_index, chunk_text
  └── embedding (VectorField, 2048 维)
```

## Celery 异步任务

| 任务名 | 触发方式 | 说明 |
|--------|----------|------|
| `memory.generate_embedding` | 记忆创建/更新时投递 | 生成单条记忆的 embedding 向量 |
| `memory.retry_failed_embeddings` | Beat: 每 5 分钟 | 扫描并重试失败/超时的 embedding |
| `memory.generate_daily_summary` | Beat: 每天 00:00 | 每日记忆总结（降级: compaction -> message -> 跳过） |
| `memory.generate_monthly_summary` | Beat: 每月 1 日 00:00 | 每月记忆总结（降级: daily-summary -> message -> 跳过） |

## 配置常量

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `MEMORY_EMBEDDING_PENDING_TIMEOUT` | 300s | pending 状态超时阈值 |
| `MEMORY_CONTENT_MAX_LENGTH` | 10000 | 记忆内容最大字符数 |
| `MEMORY_EMBEDDING_DIMENSION` | 2048 | Embedding 向量维度 |
| `MEMORY_SEARCH_TOP_K` | 5 | 搜索返回最大条数 |
| `MEMORY_VECTOR_WEIGHT` | 0.7 | 混合搜索向量权重 |
| `MEMORY_KEYWORD_WEIGHT` | 0.3 | 混合搜索关键词权重 |
| `MEMORY_EMBEDDING_MAX_RETRY` | 3 | Embedding 最大重试次数 |
| `COMPRESS_LOCK_TIMEOUT` | 60s | 上下文压缩分布式锁超时 |

## 混合搜索评分

```
final_score = vector_score * 0.7 + keyword_score * 0.3
```

向量搜索使用 pgvector CosineDistance，关键词搜索使用 PostgreSQL 全文检索（jiebacfg 配置）。Embedding 不可用时降级为纯关键词搜索。

## LangGraph 工具集

通过 `MEMORY_TOOLS` 导出供 Agent 使用：

- `mem_search` - 搜索用户记忆
- `mem_cache` - 保存新记忆
- `mem_update` - 更新记忆内容
- `mem_delete` - 删除记忆
