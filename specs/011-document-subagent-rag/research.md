# Research: 011-document-subagent-rag

**Date**: 2026-03-05

## R1: MediaAttachment 模型扩展方案

**Decision**: 在现有 MediaAttachment 模型上新增 5 个 nullable 字段 + 1 个 default 字段，ALTER TABLE 零回填
**Rationale**: 现有模型已有 `media_type='document'` 支持，新增字段不影响现有数据，nullable 字段不锁表
**Alternatives considered**:
- 新建独立 DocumentCache 表：增加 JOIN 复杂度，违背 DRY
- 使用 JSONField 存储解析元数据：失去索引和类型安全

**现有字段参考**:
```
attachment_id (PK), attachment_uuid (unique), message (FK→Message, SET_NULL),
user_id, media_type, mime_type, file_name, file_size, storage_path,
width, height, duration_seconds, is_expired, created_at, expires_at
```

## R2: DocumentChunkEmbedding 模型设计

**Decision**: 仿照 UserMemoryEmbedding 设计，VectorField(dimensions=1024)，user_id 冗余存储
**Rationale**: 与记忆模块共享 Embedding 模型和维度，user_id 冗余加速查询（避免 JOIN）
**Alternatives considered**:
- 共用 UserMemoryEmbedding 表：职责混淆，文档和记忆生命周期不同
- 使用外部向量数据库（Milvus/Pinecone）：增加运维复杂度，pgvector 已满足规模需求

## R3: SubAgent 注册模式

**Decision**: 在 `__init__.py` 中新增 `from .document_agent import document_subagent` 并 append 到 tools 列表
**Rationale**: 与现有 SubAgent（memory/search/code/ha/multimodal）注册方式完全一致
**Alternatives considered**: 无需考虑其他方案，现有模式成熟稳定

## R4: 双写一致性策略

**Decision**: MinIO 先写 → DB 原子更新 → 失败时补偿删除 MinIO 文件
**Rationale**: 与现有 MediaService._upload_and_persist() 补偿模式一致（constitution 1.3 要求）
**Alternatives considered**:
- DB 先写 → MinIO 后写：DB 成功但 MinIO 失败时回滚更复杂
- 两阶段提交：过度工程化，单用户系统无需

## R5: 分块策略

**Decision**: Markdown 标题分段 → 段落拆分 → 长段切分（chunk_size=800, overlap=100）
**Rationale**: 文档解析结果为 Markdown 格式，按标题分段保持语义完整性；800 字符适配 1024 维 Embedding 上下文窗口
**Alternatives considered**:
- 固定字符切分：破坏语义完整性
- 基于 Token 的分块：增加 tokenizer 依赖，字符近似已足够
- RecursiveCharacterTextSplitter：引入 LangChain 分块器，但自定义逻辑更贴合 Markdown 结构

## R6: 混合搜索 Rerank 策略

**Decision**: combined_score = vector_score × 0.7 + keyword_score × 0.3，与记忆模块一致
**Rationale**: 复用 MemoryService.search_memory() 的成熟权重配置，语义优先符合自然语言查询场景
**Alternatives considered**:
- 纯语义搜索：无法处理精确关键词匹配
- 倒排索引 + BM25：需要额外基础设施
- LLM Rerank：延迟高，单用户场景不值得

## R7: Celery 任务复用模式

**Decision**: 仿照 memory.generate_embedding 和 memory.retry_failed_embeddings 模式
**Rationale**: GPU 互斥（has_active_users()）、重试逻辑、Beat 调度均可直接复用
**Alternatives considered**: 无需考虑其他方案

## R8: 强制重新解析实现

**Decision**: document_parse 工具新增 force=True 参数，清除旧缓存后重新解析
**Rationale**: 澄清阶段确认需要支持手动刷新缓存（解析质量不满意或模型升级）
**清除范围**: parsed_content + parsed_content_path(MinIO) + DocumentChunkEmbedding 级联删除 + embedding_status 重置
