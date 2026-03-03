# tests/memory 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖功能 |
|------|----------|
| `test_models.py` | UserMemory / UserMemoryEmbedding 模型（字段默认值/类型选择/级联删除） |
| `test_repositories.py` | MemoryRepository CRUD / 分页 / 类型过滤 / 重试查询 / EmbeddingRepository 向量搜索骨架 |
| `test_services.py` | MemoryService 创建/更新/删除/查询 / Embedding 客户端配置 / Celery 降级 |
| `test_tasks.py` | Celery 任务: generate_embedding（成功/重试/替换）/ retry_failed / daily_summary |
| `test_tools.py` | Agent 记忆工具: mem_search / mem_cache / mem_update / mem_delete / user_id 注入 |
| `test_views.py` | REST API 视图: 列表/创建/详情/更新/删除 / 分页 / 类型过滤 / 响应格式 |
| `test_isolation.py` | 跨用户隔离: Repository/Service/View 三层均验证 user_id 隔离 |
| `test_performance.py` | 性能测试: Tokenizer 大文本 / 上下文裁剪 / PromptBuilder 构建速度 |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/memory/ -v
```

## 核心 Mock

| Mock 目标 | 用途 |
|-----------|------|
| `apps.memory.repositories.MemoryRepository` | 数据库操作 |
| `apps.memory.services.generate_embedding.delay` | Celery 异步 Embedding 任务 |
| `httpx.AsyncClient` | Embedding API 调用 |
| `apps.memory.tasks._get_active_users` | 活跃用户检测（GPU 锁） |
| `APIRequestFactory` / `force_authenticate` | DRF 视图测试 |

## 注意事项

1. `test_models.py` / `test_repositories.py` / `test_isolation.py` 需要真实 PostgreSQL（`--reuse-db`）
2. `test_tasks.py` mock 了 Celery task dispatch 和 Embedding API 调用
3. `test_tools.py` 验证 user_id 从 LangGraph config 注入，缺失时抛出异常
4. `test_isolation.py` 是关键安全测试，确保三层架构均无法跨用户访问数据
5. `test_performance.py` 验证大文本 Token 计数和 PromptBuilder 性能
6. daily_summary 任务测试覆盖有活跃用户跳过 / 无活跃用户执行 / compaction 流程
