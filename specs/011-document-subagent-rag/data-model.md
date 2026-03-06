# Data Model: 011-document-subagent-rag

**Date**: 2026-03-05

## Entity Relationship

```
MediaAttachment (existing, extended)
  │ 1
  │
  └──< N  DocumentChunkEmbedding (new)
         ├─ attachment (FK → MediaAttachment, CASCADE)
         ├─ user_id (冗余, 加速查询)
         ├─ chunk_index (分块序号)
         ├─ chunk_text (分块文本)
         └─ embedding (1024 维向量)
```

## MediaAttachment — 新增字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `parsed_content` | TextField | null=True, blank=True | 解析结果全文（PostgreSQL TEXT，支持 ILIKE / GIN 全文索引） |
| `parsed_content_path` | CharField(500) | null=True, blank=True | MinIO 备份路径 (`parsed/{user_id}/{date}/{uuid}.md`) |
| `parsed_at` | DateTimeField | null=True, blank=True | 解析完成时间 |
| `parsed_content_size` | BigIntegerField | null=True, blank=True | 解析结果字节数 |
| `embedding_status` | CharField(20) | default='none' | 分块 Embedding 状态 |

**embedding_status 状态机**:
```
none → pending → processing → done
                            → failed → pending (重试)
```

**索引**:
- GIN 全文索引: `to_tsvector('jiebacfg', parsed_content) WHERE parsed_content IS NOT NULL`

**设计约束**:
- 全部 nullable（embedding_status 除外），ALTER TABLE 零回填
- 文件过期时不清除解析字段，仅标记 `is_expired=True`
- 强制重解析时清除 5 个字段 + 级联删除 chunk

## DocumentChunkEmbedding — 新表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BigAutoField | PK | 主键 |
| `attachment` | ForeignKey(MediaAttachment) | CASCADE, related_name="chunk_embeddings" | 所属文档 |
| `user_id` | BigIntegerField | db_index=True | 冗余用户 ID，加速查询 |
| `chunk_index` | IntegerField | default=0 | 分块序号 |
| `chunk_text` | TextField | - | 分块文本 |
| `embedding` | VectorField(1024) | null=True | 1024 维语义向量 |
| `created_at` | DateTimeField | auto_now_add=True | 创建时间 |

**Meta**:
- `db_table = "document_chunk_embedding"`
- 索引: `idx_dce_attachment` (attachment_id), `idx_dce_user` (user_id)

**设计约束**:
- `user_id` 冗余存储（同 UserMemoryEmbedding），所有查询必须带 `user_id` 隔离
- `on_delete=CASCADE`：文档附件删除时级联删除 chunk
- 文件过期 ≠ 删除，过期不触发 CASCADE
- 维度 1024 对齐 `settings.MEMORY_EMBEDDING_DIMENSION`

## 数据生命周期

| 事件 | MediaAttachment | parsed_content | MinIO parsed/ | DocumentChunkEmbedding |
|------|----------------|----------------|---------------|----------------------|
| 首次解析 | 不变 | 写入全文 | 上传 .md | Celery 异步创建 |
| 缓存命中 | 不变 | 直接读取 | 不访问 | 不访问 |
| 原始文件过期 | is_expired=True | **保留** | 原始文件删除，parsed .md **保留** | **保留** |
| 强制重解析 | 不变 | 清除→重写 | 删除→重传 | 删除→重建 |
| 附件硬删除 | DELETE | CASCADE | 需手动清理 | CASCADE |

## 存储架构

```
PostgreSQL (主存储)
  ├─ media_attachment.parsed_content    ← 全文，ILIKE + GIN 分词搜索
  └─ document_chunk_embedding           ← 分块 + pgvector 向量搜索

MinIO (备份)
  └─ linchat-media/parsed/{user_id}/{YYYY-MM-DD}/{attachment_uuid}.md

双写一致性:
  写入: MinIO 成功 → DB 成功 → 完成
        MinIO 成功 → DB 失败 → 补偿删除 MinIO 文件
        MinIO 失败 → 不写 DB → 返回失败
  读取优先级: DB parsed_content → MinIO parsed_content_path（降级）
```

## 配置项 (core/settings.py)

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `DOCUMENT_SUBAGENT_TIMEOUT` | 1200 | 文档 SubAgent 超时（秒） |
| `DOC_CHUNK_SIZE` | 800 | 分块大小（字符） |
| `DOC_CHUNK_OVERLAP` | 100 | 分块重叠（字符） |
| `DOC_VECTOR_WEIGHT` | 0.7 | 混合搜索向量权重 |
| `DOC_KEYWORD_WEIGHT` | 0.3 | 混合搜索关键词权重 |
| `DOC_SEARCH_TOP_K` | 5 | 搜索结果上限 |
| `DOC_PARSE_MAX_RESULT_LENGTH` | 6000 | document_parse 工具返回结果最大字符数 |
