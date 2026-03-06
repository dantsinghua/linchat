# Implementation Plan: 文档 SubAgent + 解析结果持久化 + 轻量 RAG

**Branch**: `011-document-subagent-rag` | **Date**: 2026-03-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/011-document-subagent-rag/spec.md`

## Summary

将文档解析从一次性使用升级为持久化存储 + 独立 SubAgent + 轻量 RAG 检索。核心变更：

1. **解析结果持久化**: MediaAttachment 扩展 5 个字段，解析结果双写 PostgreSQL + MinIO，支持缓存复用（2-5 分钟 → <1 秒）
2. **文档分块 + Embedding**: 新增 DocumentChunkEmbedding 表（pgvector 1024 维），Celery 异步分块 + 向量生成
3. **轻量 RAG**: 关键词 + 语义 + 混合检索 + rerank，复用记忆模块的 Embedding 和搜索模式
4. **独立 document_subagent**: 从 multimodal_subagent 拆分，4 个工具（doc_list/doc_read/doc_search/document_parse）
5. **强制重解析**: document_parse 支持 force=True 清除旧缓存并重建索引

## Technical Context

**Language/Version**: Python 3.11+ / Django 4.2+ / DRF 3.14+
**Primary Dependencies**: LangGraph, LangChain, pgvector, Celery 5.3+, MinIO, Redis, httpx, Jinja2, websockets
**Storage**: PostgreSQL 15 (pgvector extension), Redis (缓存/频率限制), MinIO (对象存储)
**Testing**: pytest + pytest-django
**Target Platform**: Linux server (单一生产环境)
**Project Type**: Web application (Django 后端，本特性仅涉及后端)
**Performance Goals**: 缓存命中 <1s，RAG 搜索 <5s
**Constraints**: 单用户系统，GPU 互斥（has_active_users），embedding 维度 1024
**Scale/Scope**: 单用户文档量级 ~100 篇，每篇文档 ~10-50 个分块

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 合规状态 | 说明 |
|------|------|----------|------|
| 1.1 关注点分离 | 分层架构 | ✅ 通过 | Repository 层封装 ORM，Service 层封装业务逻辑，SubAgent 层封装工具 |
| 1.3 数据一致性 | 双写原子性 | ✅ 通过 | MinIO 先写 → DB 原子更新 → 失败补偿删除 MinIO |
| 2.1 Python 规范 | PEP8 + Black + 类型注解 | ✅ 通过 | 所有新代码遵循 |
| 3.1 测试覆盖率 | 服务层 95%，总体 80% | ✅ 通过 | 计划 20+ 测试用例覆盖缓存/分块/RAG/工具 |
| 4.1 数据隔离 | user_id 粒度 | ✅ 通过 | 所有查询强制 user_id 过滤，DocumentChunkEmbedding 冗余 user_id |
| 4.3 LLM 异常 | 统一处理 | ✅ 通过 | run_subagent() 已统一处理 LLM 异常 |
| 5.1 性能 | p95 指标 | ✅ 通过 | 缓存命中 <1s，搜索延迟可接受 |
| 9.2 单用户 | 禁止并发控制 | ✅ 通过 | 无并发冲突设计，GPU 锁复用现有机制 |

**Gate 结果**: ✅ 全部通过，无违规项

## Project Structure

### Documentation (this feature)

```text
specs/011-document-subagent-rag/
├── spec.md              # 功能规范
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出
├── data-model.md        # Phase 1 数据模型设计
├── quickstart.md        # Phase 1 快速参考
├── contracts/
│   ├── document-subagent-tools.md   # SubAgent 工具契约
│   └── repository-service-api.md    # Repository/Service API 契约
├── checklists/
│   └── requirements.md  # 规范质量检查清单
└── tasks.md             # Phase 2 任务清单（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── media/
│   │   ├── models.py                    # [修改] +5 字段 + DocumentChunkEmbedding
│   │   ├── repositories.py              # [修改] +5 方法 + DocumentChunkEmbeddingRepository
│   │   ├── serializers.py               # [修改] +3 只读字段
│   │   ├── views.py                     # [修改] 缓存快速返回
│   │   ├── tasks.py                     # [修改] +embedding 任务 +重试 +清理变更
│   │   ├── services/
│   │   │   └── document.py              # [修改] +缓存读写 +双写 +分块 +RAG 搜索
│   │   └── migrations/
│   │       └── 0002_add_document_cache_and_chunks.py  # [新增]
│   ├── graph/
│   │   └── subagents/
│   │       ├── __init__.py              # [修改] 注册 document_subagent
│   │       ├── document_agent.py        # [新增] 文档 SubAgent + 4 工具
│   │       └── multimodal_agent.py      # [修改] 移除 document_parse
│   └── context/
│       └── templates/
│           └── document_subagent.j2     # [新增] SubAgent Prompt 模板
├── core/
│   ├── settings.py                      # [修改] +6 配置项
│   └── celery.py                        # [修改] +retry_failed_doc_embeddings Beat
└── tests/
    ├── media/
    │   ├── test_document_cache.py       # [新增] 缓存读写测试
    │   ├── test_document_chunk.py       # [新增] 分块策略测试
    │   └── test_document_rag.py         # [新增] RAG 搜索测试
    └── graph/
        └── test_document_agent.py       # [新增] SubAgent 工具测试
```

**Structure Decision**: 纯后端变更，遵循现有 Django apps 分层架构。新增文件仅 2 个（document_agent.py + document_subagent.j2），其余均为现有文件扩展。

## Complexity Tracking

> 无宪法违规项，无需记录。
