# Memory 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 用户长期记忆管理模块：CRUD、Embedding 向量生成、混合语义搜索、定时总结。

---

## 文件清单

| 文件 | 职责 | 备注 |
|------|------|------|
| `models.py` | UserMemory（元数据）+ UserMemoryEmbedding（1024 维向量） | |
| `repositories.py` | MemoryRepository + EmbeddingRepository（ORM + pgvector + 全文检索） | |
| `services.py` | EmbeddingClient（向量生成）+ MemoryService（CRUD/搜索/总结） | |
| `task_helpers.py` | Celery 任务辅助函数：活跃用户检查、模型预热、内容收集、总结执行 | **新增** |
| `tasks.py` | Celery 异步任务：embedding 生成、重试、每日/每月总结、健康检查 | |
| `views.py` | REST API：列表/创建、详情/更新/删除、搜索 | |
| `serializers.py` | 6 个序列化器（Create/Update/Response/Search/SearchResult/ListQuery） | |
| `urls.py` | API 路由（`/memories/`、`/memories/<id>/`、`/memories/search/`） | |
| `apps.py` | Django App 配置 | |

---

## 核心逻辑

### 记忆类型枚举

`memory`（用户记忆）/ `compaction`（上下文压缩）/ `daily-summary` / `monthly-summary`

### 混合搜索

`final_score = vector_score x 0.7 + keyword_score x 0.3`

- 向量搜索：pgvector CosineDistance，仅 `embedding_status='done'`
- 关键词搜索：PostgreSQL jiebacfg 全文检索
- 降级：`skip_vector=True` 或 API 异常时仅关键词

### GPU 互斥机制

`task_helpers.py` 实现单 GPU 环境下 Embedding/语言模型互斥：
- `has_active_users()` — Redis `auth:token:*` 扫描，有用户在线时跳过
- `warmup_language_model()` — embedding 成功后发最小请求，强制 vLLM 加载回 GPU
- `collect_content()` — 收集总结数据（primary_type -> message 降级 -> unknown 用户语音消息）
- `run_summary()` — 查找活跃用户 -> 收集 -> LLM 事实抽取 -> 存储

### 定时总结

| 任务 | 调度 | 数据源优先级 |
|------|------|-------------|
| 每日总结 | 每天 00:00 | compaction -> message -> 跳过 |
| 每月总结 | 每月 1 日 | daily-summary -> message -> 跳过 |

---

## 用户隔离 [R-004]

- Repository: 所有查询 WHERE 必带 `user_id`，缺失抛 `ValueError`
- Service: 跨用户访问抛 `MemoryNotFoundError`
- View: 跨用户返回 404（隐藏资源存在性）

---

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.models.services` | `get_active_model("embedding"/"tool")` |
| `apps.graph.agent` | `get_llm()` 用于事实抽取 |
| `apps.graph.prompts` | `CRONMEM_PROMPT_TEMPLATE` |
| `apps.chat.models` | Message 模型用于总结降级数据源 |
| `apps.users.models` | SysUser 查找 unknown 用户 |
| pgvector + pg_jieba | 向量搜索 + 中文全文检索 |
| Celery + Redis DB2 | 异步任务队列 |

---

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/memory/ -v
pytest tests/memory/ --cov=apps.memory --cov-report=term-missing
```
