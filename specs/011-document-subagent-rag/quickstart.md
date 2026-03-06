# Quickstart: 011-document-subagent-rag

**Date**: 2026-03-05

## 实施顺序

```
Phase 1: 数据层 (模型 + 迁移 + Repository)
  T001 → T002 → T003

Phase 2: 服务层 (缓存读写 + 双写 + 分块 + RAG 搜索)
  T004 → T005 → T006

Phase 3: Celery 任务 (Embedding 生成 + 重试 + 清理变更)
  T007 → T008

Phase 4: SubAgent 层 (模板 + 工具 + 注册 + 多模态精简)
  T009 → T010 → T011 → T012

Phase 5: 配置 + 序列化器 + 视图
  T013

Phase 6: 测试
  T014 → T015
```

## 涉及文件

| 文件 | 操作 | Phase |
|------|------|-------|
| `apps/media/models.py` | 修改 | 1 |
| `apps/media/migrations/0002_*.py` | 新增 | 1 |
| `apps/media/repositories.py` | 修改 | 1 |
| `apps/media/services/document.py` | 修改 | 2 |
| `apps/media/tasks.py` | 修改 | 3 |
| `core/celery.py` | 修改 | 3 |
| `core/settings.py` | 修改 | 5 |
| `apps/context/templates/document_subagent.j2` | **新增** | 4 |
| `apps/graph/subagents/document_agent.py` | **新增** | 4 |
| `apps/graph/subagents/multimodal_agent.py` | 修改 | 4 |
| `apps/graph/subagents/__init__.py` | 修改 | 4 |
| `apps/media/serializers.py` | 修改 | 5 |
| `apps/media/views.py` | 修改 | 5 |
| `tests/media/test_document_*.py` | **新增** | 6 |
| `tests/graph/test_document_agent.py` | **新增** | 6 |

## 复用清单

| 函数/模式 | 文件 | 说明 |
|-----------|------|------|
| `EmbeddingClient.generate_embedding()` | `apps/memory/services.py` | 1024 维向量生成 |
| `CosineDistance` | `pgvector.django` | 向量相似度搜索 |
| `SearchVector/SearchRank + jiebacfg` | `apps/memory/repositories.py` | 中文全文检索 |
| `has_active_users()` | `apps/memory/task_helpers.py` | GPU 互斥检查 |
| `run_subagent()` | `apps/graph/subagents/base.py` | SubAgent 工厂 |
| `_get_user_id()` | `apps/graph/subagents/base.py` | 提取 user_id |
| `acquire_gpu_lock()` | `apps/graph/services/gpu_lock.py` | GPU 锁 |
| `loader.render()` | `apps/context/loader.py` | Jinja2 渲染 |
| `minio_service.*` | `apps/common/storage/minio_service.py` | MinIO 操作 |
| `media_attachment_repo.*` | `apps/media/repositories.py` | 附件 ORM |

## 验证方式

1. `pytest` 全量通过（无回归）
2. 上传 PDF → 首次解析 → 双写 DB+MinIO → Celery embedding → 日志确认
3. 再次提问同一文档 → 秒级返回（日志 `Doc cache hit`）
4. 对话"我上传过哪些文档" → doc_list 列表
5. 对话"之前那篇论文的核心观点" → doc_list + doc_read
6. 对话"哪篇文档提到了金融监管" → doc_search(mode=hybrid)
7. 检查 PostgreSQL `parsed_content` 和 MinIO `parsed/` 内容一致
8. 检查 `document_chunk_embedding` 表有正确的分块和向量数据
