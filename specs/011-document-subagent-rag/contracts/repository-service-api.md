# Contract: Repository & Service Layer API

**Date**: 2026-03-05

## Repository 层

### MediaAttachmentRepository — 新增方法

#### `search_documents(user_id, **kwargs) → list[MediaAttachment]`

```python
@sync_to_async
def search_documents(
    self,
    user_id: int,
    file_name: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    has_parsed: bool | None = None,
    order_by: str = "-created_at",
    limit: int = 20,
) -> list[MediaAttachment]:
```

- 基础过滤: `media_type='document'`, `user_id=user_id`
- **不过滤 `is_expired`**
- file_name 分词搜索: 按空格拆分 → 多个 `icontains` AND
- order_by 映射: `newest→-created_at`, `oldest→created_at`, `name→file_name`, `size→-file_size`

#### `update_parsed_cache(attachment_id, ...) → int`

```python
@sync_to_async
def update_parsed_cache(
    self,
    attachment_id: int,
    parsed_content: str,
    parsed_content_path: str,
    parsed_at: datetime,
    parsed_content_size: int,
) -> int:
```

- `filter(attachment_id=).update()` 原子写入 5 个字段

#### `update_embedding_status(attachment_id, status) → int`

```python
@sync_to_async
def update_embedding_status(self, attachment_id: int, status: str) -> int:
```

#### `clear_parsed_cache(attachment_id) → int`

```python
@sync_to_async
def clear_parsed_cache(self, attachment_id: int) -> int:
```

- 清除 `parsed_content`, `parsed_content_path`, `parsed_at`, `parsed_content_size`
- 重置 `embedding_status='none'`

#### `fulltext_search_parsed_content(user_id, query_text, limit=10) → list[tuple]`

```python
@sync_to_async
def fulltext_search_parsed_content(
    self,
    user_id: int,
    query_text: str,
    limit: int = 10,
) -> list[tuple[int, str, str, float]]:
```

- 降级搜索：当 chunk 尚未生成时，直接搜索 `MediaAttachment.parsed_content` 的 GIN 全文索引
- `SearchVector("parsed_content", config="jiebacfg")` + `SearchRank`
- 过滤: `media_type='document'`, `user_id=user_id`, `parsed_content__isnull=False`
- 返回 `[(attachment_id, attachment_uuid, file_name, score)]`
- 用于 `search_documents_rag()` 在 chunk 搜索无结果时的降级路径（覆盖 US3-AS3 过渡状态）

---

### DocumentChunkEmbeddingRepository — 新增类

#### `bulk_create_chunks(chunks) → list[DocumentChunkEmbedding]`

```python
@sync_to_async
def bulk_create_chunks(
    self, chunks: list[DocumentChunkEmbedding]
) -> list[DocumentChunkEmbedding]:
```

- `bulk_create()` 批量插入

#### `delete_by_attachment_id(attachment_id) → int`

```python
@sync_to_async
def delete_by_attachment_id(self, attachment_id: int) -> int:
```

#### `vector_search(user_id, query_embedding, limit=10) → list[tuple]`

```python
@sync_to_async
def vector_search(
    self,
    user_id: int,
    query_embedding: list[float],
    limit: int = 10,
) -> list[tuple[int, int, str, float]]:
```

- `CosineDistance` 排序，仅 `embedding__isnull=False`
- 返回 `[(attachment_id, chunk_index, chunk_text, score)]`
- score = 1.0 - cosine_distance

#### `keyword_search(user_id, query_text, limit=10) → list[tuple]`

```python
@sync_to_async
def keyword_search(
    self,
    user_id: int,
    query_text: str,
    limit: int = 10,
) -> list[tuple[int, int, str, float]]:
```

- `SearchVector("chunk_text", config="jiebacfg")` + `SearchRank`
- 返回 `[(attachment_id, chunk_index, chunk_text, score)]`

---

## Service 层

### DocumentParseService — 新增/修改方法

#### `get_cached_result(attachment) → Optional[str]`

```python
@staticmethod
async def get_cached_result(
    attachment: MediaAttachment,
) -> str | None:
```

- `parsed_content` 非空 → 直接返回（**不检查 `is_expired`**）
- `parsed_content` 为空但 `parsed_content_path` 非空 → 降级从 MinIO 下载
- 全为空 → None

#### `save_parsed_result(attachment, content) → bool`

```python
@staticmethod
async def save_parsed_result(
    attachment: MediaAttachment,
    content: str,
) -> bool:
```

- MinIO 上传 `parsed/{user_id}/{YYYY-MM-DD}/{attachment_uuid}.md`
- DB 原子更新 5 个字段 + `embedding_status='pending'`
- 补偿: MinIO 失败不写 DB，DB 失败删 MinIO
- 成功后 dispatch `generate_document_embeddings.delay(attachment_id)`

#### `clear_parsed_cache(attachment) → None`

```python
@staticmethod
async def clear_parsed_cache(
    attachment: MediaAttachment,
) -> None:
```

- 删除 MinIO 备份文件（忽略 NotFound）
- 删除 DocumentChunkEmbedding 记录
- 清除 DB 解析字段

#### `search_documents_rag(user_id, query, mode='hybrid', limit=5) → list[dict]`

```python
@staticmethod
async def search_documents_rag(
    user_id: int,
    query: str,
    mode: str = "hybrid",
    limit: int = 5,
) -> list[dict]:
```

- 三种模式: keyword / semantic / hybrid
- hybrid: vector_search + keyword_search → rerank → top-k
- 降级: 向量异常时自动降级为 keyword
- 返回: `[{"file_name", "attachment_uuid", "created_at", "chunk_text", "score", "match_type"}]`

#### `chunk_document(content, chunk_size=800, overlap=100) → list[str]`

```python
@staticmethod
def chunk_document(
    content: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
```

- 按 Markdown 标题分段 → 段落拆分 → 合并小段 → 切分长段

---

## Celery 任务

### `generate_document_embeddings(attachment_id)`

```python
@shared_task(name="media.generate_document_embeddings")
def generate_document_embeddings(attachment_id: int) -> None:
```

- 查询 MediaAttachment → `parsed_content` 非空
- `embedding_status` → `processing`
- `chunk_document()` → chunks
- 对每个 chunk: `EmbeddingClient.generate_embedding()`
- `bulk_create_chunks()`
- `embedding_status` → `done` / `failed`

### `retry_failed_doc_embeddings()`

```python
@shared_task(name="media.retry_failed_doc_embeddings")
def retry_failed_doc_embeddings() -> None:
```

- 每 5 分钟，扫描 `embedding_status='failed'` 的文档
- 重新 dispatch `generate_document_embeddings`

---

## Serializer 扩展

### MediaAttachmentSerializer — 新增只读字段

```python
fields = [
    ...,  # 现有字段
    "parsed_at",
    "parsed_content_size",
    "embedding_status",
]
```

不暴露 `parsed_content`（太大）和 `parsed_content_path`（内部路径）。

---

## 视图层变更

### `parse_document` 视图 — 缓存快速返回

无 `pages` 参数时先检查缓存:
```python
if not pages:
    cached = await document_parse_service.get_cached_result(attachment)
    if cached:
        return Response({"cached": True, "content": cached[:max_len], "format": "markdown"})
```
