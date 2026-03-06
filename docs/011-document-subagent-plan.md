# 011 — 文档 SubAgent + 解析结果持久化 + 轻量 RAG

## Context

当前文档解析（MiniCPM-o OCR）结果是一次性的：Gateway 返回 Markdown → Agent 使用 → 存入 Message.content（AI 摘要）→ 原始解析全文丢弃。同一 PDF 再次提问需重新解析（2-5 分钟）。`document_parse` 嵌套在 `multimodal_subagent` 内部，用户无法独立查询已有文档。

**目标**：

1. 解析结果双写 PostgreSQL + MinIO，保持一致性，原子变更
2. **文件过期后解析结果仍保留**（原始文件可过期删除，解析内容持久存在）
3. 解析结果分块 + Embedding 存储（pgvector），支持语义检索
4. 从 multimodal_subagent 拆分出独立 **document_subagent**，`doc_search` 升级为轻量 RAG（关键词 + 语义 + 混合检索 + rerank）
5. 支持"之前那篇文档再总结一下"、"前两篇文档综合比较一下"等跨消息操作

---

## 方案总览

```
主 Agent
  ├── multimodal_subagent → multimodal_analyze（图片/视频/音频）
  ├── document_subagent   → doc_list + doc_read + doc_search + document_parse   ← 新增
  ├── search_subagent / memory_subagent / code_subagent / ha_subagent / history_search
  └── ...

存储架构（双写一致）:
  解析全文 → PostgreSQL parsed_content (TEXT)   ← 主存储，ILIKE 分词搜索
           + MinIO parsed/{user_id}/{date}/{uuid}.md  ← 备份，大文件下载
  解析分块 → PostgreSQL document_chunk_embedding  ← pgvector 语义搜索

缓存流程:
  缓存命中:  document_parse → DB parsed_content 非空 → 直接返回（<1s）
  缓存未命中: document_parse → GPU 锁 → Gateway 解析 → 双写 DB+MinIO → Celery 分块+Embedding → 返回

doc_search 轻量 RAG:
  用户查询 → Embedding → 向量搜索（CosineDistance）
                       → 关键词搜索（jiebacfg tsvector / ILIKE 分词）
                       → 混合打分 rerank（vector_weight × 0.7 + keyword_weight × 0.3）
                       → 返回 top-k 文档片段 + 上下文
```

---

## 新增数据模型

### MediaAttachment 新增字段 — `apps/media/models.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `parsed_content` | TextField(null) | 解析结果全文（PostgreSQL TEXT，支持 ILIKE / GIN 全文索引） |
| `parsed_content_path` | CharField(500, null) | MinIO 备份路径 |
| `parsed_at` | DateTimeField(null) | 解析完成时间 |
| `parsed_content_size` | BigIntegerField(null) | 解析结果字节数 |
| `embedding_status` | CharField(20, default='none') | 分块 Embedding 状态：none/pending/processing/done/failed |

全部 nullable（embedding_status 除外），ALTER TABLE 零回填。

### DocumentChunkEmbedding（新表）— `apps/media/models.py`

仿照 `UserMemoryEmbedding` 设计，表名 `document_chunk_embedding`：

```python
class DocumentChunkEmbedding(models.Model):
    id = models.BigAutoField(primary_key=True)
    attachment = models.ForeignKey(MediaAttachment, on_delete=models.CASCADE,
                                    related_name="chunk_embeddings")
    user_id = models.BigIntegerField(db_index=True)       # 冗余，加速查询
    chunk_index = models.IntegerField(default=0)           # 分块序号
    chunk_text = models.TextField()                        # 分块文本
    embedding = VectorField(dimensions=1024, null=True)    # 1024 维向量
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_chunk_embedding"
        indexes = [
            models.Index(fields=["attachment_id"], name="idx_dce_attachment"),
            models.Index(fields=["user_id"], name="idx_dce_user"),
        ]
```

**关键设计**：
- `user_id` 冗余存储（同 UserMemoryEmbedding），所有查询必须带 `user_id` 隔离
- `on_delete=CASCADE`：文档删除时级联删除 chunk（但文件过期≠删除，见下方）
- 维度 1024 对齐 `settings.MEMORY_EMBEDDING_DIMENSION`
- 复用已有 `EmbeddingClient.generate_embedding()` 生成向量

---

## 迁移 — `apps/media/migrations/0002_add_document_cache_and_chunks.py`

`makemigrations` 生成，包含：
- AddField × 5（MediaAttachment 新字段）
- CreateModel（DocumentChunkEmbedding）
- GIN 全文索引（parsed_content 的 tsvector）

```sql
CREATE INDEX idx_ma_parsed_tsv
ON media_attachment
USING GIN (to_tsvector('jiebacfg', parsed_content))
WHERE parsed_content IS NOT NULL;
```

---

## Repository 层 — `apps/media/repositories.py`

### MediaAttachmentRepository 新增方法

**`search_documents(user_id, file_name=None, created_after=None, created_before=None, has_parsed=None, order_by='-created_at', limit=20)`**
- 基础过滤：`media_type='document'`, `user_id=user_id`
- **不过滤 `is_expired`**（解析结果在过期后仍可查询）
- `file_name` **分词搜索**：按空格拆分 → 多个 `Q(file_name__icontains=kw)` AND
- `created_after` / `created_before`：时间范围（`created_at__gte/lte`）
- `has_parsed`：True → `parsed_content__isnull=False`
- 排序映射：`newest` → `-created_at`、`oldest` → `created_at`、`name` → `file_name`、`size` → `-file_size`

**`update_parsed_cache(attachment_id, parsed_content, parsed_content_path, parsed_at, parsed_content_size)`**
- `filter(attachment_id=).update()` 原子写入 5 个字段

**`update_embedding_status(attachment_id, status)`**
- 更新 `embedding_status` 字段

### DocumentChunkEmbeddingRepository（新增类）

**`bulk_create_chunks(chunks: list[DocumentChunkEmbedding])`**
- `bulk_create()` 批量插入

**`delete_by_attachment_id(attachment_id)`**
- 删除指定文档的全部 chunk

**`vector_search(user_id, query_embedding, limit=10)`**
- `CosineDistance` 排序，仅 `embedding__isnull=False`
- 返回 `[(attachment_id, chunk_index, chunk_text, score)]`

**`keyword_search(user_id, query_text, limit=10)`**
- 方案 A：`SearchVector("chunk_text", config="jiebacfg")` + `SearchRank`
- 返回 `[(attachment_id, chunk_index, chunk_text, score)]`

---

## 服务层 — `apps/media/services/document.py`

### 缓存读写

**`get_cached_result(attachment) → Optional[str]`**
- `parsed_content` 非空 → 直接返回（**不检查 `is_expired`**，过期文件的解析结果仍有效）
- `parsed_content` 为空但 `parsed_content_path` 非空 → 降级从 MinIO 下载
- 全为空 → None

**`save_parsed_result(attachment, content) → bool`**
- **双写原子性**：
  1. MinIO 上传 `parsed/{user_id}/{YYYY-MM-DD}/{attachment_uuid}.md`
  2. DB 更新 `parsed_content`, `parsed_content_path`, `parsed_at`, `parsed_content_size`, `embedding_status='pending'`
  3. 任一失败 → 回滚另一方（MinIO 失败不写 DB，DB 失败删 MinIO）
- **保存截断前的完整内容**
- 成功后 dispatch Celery 任务 `generate_document_embeddings(attachment_id)`

### 轻量 RAG 搜索

**`search_documents_rag(user_id, query, mode='hybrid', limit=5) → list[dict]`**

三种检索模式：

| 模式 | 说明 |
|------|------|
| `keyword` | 仅关键词（分词 ILIKE 或 tsvector） |
| `semantic` | 仅语义（Embedding + CosineDistance） |
| `hybrid` | 混合检索 + rerank（默认） |

**hybrid 混合检索流程**：
```
1. query_embedding = EmbeddingClient.generate_embedding(query)
2. vector_results = chunk_embedding_repo.vector_search(user_id, query_embedding, limit*3)
3. keyword_results = chunk_embedding_repo.keyword_search(user_id, query, limit*3)
4. rerank:
   - 按 (attachment_id, chunk_index) 聚合去重
   - combined_score = vector_score × DOC_VECTOR_WEIGHT + keyword_score × DOC_KEYWORD_WEIGHT
   - 排序取 top-k
5. 返回 [{
     "file_name": ...,
     "attachment_uuid": ...,
     "created_at": ...,
     "chunk_text": ...,       # 匹配的分块内容
     "score": ...,            # 综合得分
     "match_type": "hybrid"   # hybrid/vector/keyword
   }]
```

**降级机制**：向量搜索异常时自动降级为纯关键词搜索（同 memory 模块）。

---

## 文档分块策略 — `apps/media/services/document.py`

**`chunk_document(content: str, chunk_size=800, overlap=100) → list[str]`**

1. 按 Markdown 标题（`## ` / `### `）分段
2. 每段内按双换行（段落）进一步拆分
3. 合并过小的段落直到达到 `chunk_size`
4. 超过 `chunk_size` 的段落按字符切分，保留 `overlap` 字符重叠
5. 每个 chunk 前缀附加文档名（`[文档: xxx.pdf] `），增强语义信息

**参数配置**（`core/settings.py`）：
- `DOC_CHUNK_SIZE = 800`
- `DOC_CHUNK_OVERLAP = 100`

---

## Celery 任务 — `apps/media/tasks.py`

### `generate_document_embeddings(attachment_id)`

仿照 `memory.tasks.generate_embedding`，流程：

```
1. 查询 MediaAttachment，确认 parsed_content 非空
2. embedding_status → 'processing'
3. chunk_document(parsed_content) → chunks[]
4. 对每个 chunk：EmbeddingClient.generate_embedding(chunk_text)
5. bulk_create DocumentChunkEmbedding 记录
6. embedding_status → 'done'
7. 失败：embedding_status → 'failed'，retry_count += 1
```

**GPU 互斥**：复用 `has_active_users()` 检查（同 memory embedding），有用户在线时延迟执行。

### 定时重试 — `retry_failed_doc_embeddings`

仿照 `memory.retry_failed_embeddings`，每 5 分钟扫描 `embedding_status='failed'` 的文档。

---

## Jinja2 Prompt 模板 — `apps/context/templates/document_subagent.j2`

```jinja2
你是文档管理助手。管理和查询用户上传的文档及其解析结果。

## 工具
- doc_list: 列出文档，支持文件名分词搜索、时间范围筛选、按时间/名称/大小排序
- doc_read: 读取指定文档的完整解析内容（需要 attachment_uuid）
- doc_search: 在所有已解析文档中检索（支持关键词、语义、混合模式 + rerank）
- document_parse: 解析新上传的 PDF/DOCX 文档（已解析过的自动返回缓存）

## 执行策略
- 用户提到"之前那篇文档"/"上次的论文" → doc_list 查找 → doc_read 读取内容
- 用户要求"比较两篇文档" → 分别 doc_read 获取 → 综合比较返回
- 用户问"哪篇文档提到了 XX" → doc_search 搜索（优先 hybrid 模式）
- 用户上传新文档需要解析 → document_parse（自动缓存 + 自动建立索引）
- 独立完成任务，返回完整结果
```

通过 `loader.render("document_subagent.j2")` 加载。

---

## 文档 SubAgent — `apps/graph/subagents/document_agent.py`

**新文件**，包含 4 个工具 + 1 个 SubAgent 入口。

### `doc_list` — 文档元数据查询

```python
@tool
async def doc_list(task: str, config: RunnableConfig,
                   file_name: str = "",
                   created_after: str = "",
                   created_before: str = "",
                   order_by: str = "newest",
                   limit: int = 10) -> str:
    """列出用户的文档附件。支持文件名分词搜索、时间范围筛选和排序。
    Args:
        task: 查询描述
        file_name: 文件名关键词（空格分隔多词 AND 匹配），为空则列出全部
        created_after: 仅显示此日期之后的文档（YYYY-MM-DD），为空不限
        created_before: 仅显示此日期之前的文档（YYYY-MM-DD），为空不限
        order_by: newest（最新）、oldest（最早）、name（名称）、size（大小）
        limit: 数量上限，默认 10，最大 20
    """
```

**分词搜索**：`"量子 计算"` → `Q(file_name__icontains="量子") & Q(file_name__icontains="计算")`

格式化输出示例：
```
找到 3 个文档：
1. [abc123] 量子计算研究报告.pdf | 2.3MB | 2026-03-04 10:30 | ✅ 已解析 | 📎 原始文件可用
2. [def456] 金融监管论文.pdf | 1.1MB | 2026-02-20 15:00 | ✅ 已解析 | ⚠️ 原始文件已过期
3. [ghi789] 新上传.docx | 0.5MB | 2026-03-05 09:00 | ❌ 未解析
```

### `doc_read` — 读取解析结果

```python
@tool
async def doc_read(attachment_uuid: str, config: RunnableConfig,
                   max_length: int = 8000) -> str:
    """读取指定文档的解析结果（Markdown 全文）。
    Args:
        attachment_uuid: 文档附件 UUID（从 doc_list 获取）
        max_length: 返回最大长度，默认 8000 字符
    """
```

从 DB `parsed_content` 直接读取。**即使原始文件已过期，解析结果仍可读取**。未解析则返回提示。

### `doc_search` — 轻量 RAG 检索

```python
@tool
async def doc_search(query: str, config: RunnableConfig,
                     mode: str = "hybrid",
                     limit: int = 5) -> str:
    """在用户所有已解析文档中检索内容。支持关键词、语义和混合检索。
    Args:
        query: 搜索查询（自然语言或关键词，空格分隔多词）
        mode: 检索模式 - keyword（关键词）、semantic（语义向量）、hybrid（混合+rerank，推荐）
        limit: 最多返回几个片段，默认 5
    """
```

调用 `DocumentParseService.search_documents_rag()`，格式化返回匹配片段 + 文档元数据 + 得分。

### `document_parse` — 文档解析（迁移+缓存+索引）

从 `multimodal_agent.py` 迁移，新增逻辑：

```python
for doc in docs:
    # 1. 检查缓存 → 命中则跳过 GPU 锁和 Gateway
    cached = await DocumentParseService.get_cached_result(doc)
    if cached:
        logger.info("Doc cache hit: file=%s", doc.file_name)
        content = cached[:max_len] + ("\n\n[内容已截断]" if len(cached) > max_len else "")
        results.append(f"## {doc.file_name}\n\n{content}")
        continue

    # 2. 未命中 → GPU 锁 + Gateway 解析
    async with acquire_gpu_lock(req_id):
        ...
        # 3. 解析成功 → 双写 DB+MinIO + dispatch Embedding 任务
        await DocumentParseService.save_parsed_result(doc, content)
        ...
```

### `document_subagent` 入口

```python
from apps.context.loader import render as render_template

DOCUMENT_PROMPT = render_template("document_subagent.j2")

@tool
async def document_subagent(task: str, config: RunnableConfig) -> str:
    """查询和管理用户的文档。查看文档列表、读取解析内容、搜索文档关键词、解析新文档时使用。"""
    cfg = config.get("configurable", {})
    uuids = cfg.get("attachment_uuids", [])
    if uuids:
        task += f"\n\n[系统：用户已上传 {len(uuids)} 个附件，如需解析文档请调用 document_parse。]"
    return await run_subagent(
        task, config,
        tools=[doc_list, doc_read, doc_search, document_parse],
        prompt=DOCUMENT_PROMPT,
        name="document_subagent",
        timeout=getattr(settings, "DOCUMENT_SUBAGENT_TIMEOUT", 1200),
    )
```

---

## 多模态 SubAgent 精简 — `apps/graph/subagents/multimodal_agent.py`

- **删除** `document_parse` 函数
- **更新** `MULTIMODAL_PROMPT`：移除文档规则，仅保留图片/视频/音频
- **更新** `multimodal_subagent` 的 tools：仅 `[multimodal_analyze]`
- **更新** docstring：`"""分析用户上传的图片、视频、音频文件。"""`

---

## SubAgent 注册 — `apps/graph/subagents/__init__.py`

```python
# 文档 SubAgent：始终启用
from .document_agent import document_subagent
tools.append(document_subagent)
```

---

## 清理任务 — `apps/media/tasks.py`

### `clean_expired_media` 修改

文件过期时的行为变更：

| 操作 | 之前 | 之后 |
|------|------|------|
| 删除 MinIO 原始文件 | ✅ | ✅（不变） |
| 标记 `is_expired=True` | ✅ | ✅（不变） |
| 删除 MinIO 解析缓存 .md | — | ❌ **保留** |
| 清空 DB `parsed_content` | — | ❌ **保留** |
| 删除 `DocumentChunkEmbedding` | — | ❌ **保留** |

**原则**：文件过期只影响原始文件下载，解析结果永久保留可查询。

### `retry_failed_doc_embeddings`（新增 Celery Beat 任务）

每 5 分钟扫描 `embedding_status='failed'` 的文档，重新 dispatch embedding 任务。

---

## 设置 — `core/settings.py`

新增配置项：

```python
# 文档 SubAgent
DOCUMENT_SUBAGENT_TIMEOUT = 1200

# 文档分块
DOC_CHUNK_SIZE = 800
DOC_CHUNK_OVERLAP = 100

# 文档 RAG 搜索权重
DOC_VECTOR_WEIGHT = 0.7
DOC_KEYWORD_WEIGHT = 0.3
DOC_SEARCH_TOP_K = 5
```

---

## Serializer — `apps/media/serializers.py`

`MediaAttachmentSerializer` 新增只读字段：`parsed_at`、`parsed_content_size`、`embedding_status`。不暴露 `parsed_content`（太大）和 `parsed_content_path`（内部路径）。

---

## 视图层 — `apps/media/views.py`

`parse_document` 视图：无 `pages` 参数时先检查缓存，命中直接返回 `{"cached": True, "content": ..., "format": "markdown"}`。

---

## 存储架构

```
                    ┌────────────────────────────────┐
                    │  PostgreSQL                     │
                    │                                 │
                    │  media_attachment               │
                    │  ├─ parsed_content (TEXT)        │ ← 全文，ILIKE + GIN 分词搜索
                    │  ├─ parsed_content_path (VARCHAR)│ ← MinIO 路径引用
                    │  ├─ parsed_at (TIMESTAMP)        │
                    │  ├─ parsed_content_size (BIGINT) │
                    │  └─ embedding_status (VARCHAR)   │
                    │                                 │
                    │  document_chunk_embedding        │
                    │  ├─ chunk_text (TEXT)             │ ← 分块文本
                    │  ├─ embedding (VECTOR 1024)       │ ← pgvector 向量
                    │  └─ chunk_index (INT)             │ ← 分块序号
                    └──────────────┬──────────────────┘
                                   │ 一致
                    ┌──────────────▼──────────────────┐
                    │  MinIO (linchat-media bucket)    │
                    │  parsed/{user_id}/{date}/         │ ← 备份（与 DB 内容一致）
                    │  {attachment_uuid}.md             │
                    └─────────────────────────────────┘

双写一致性:
  写入: MinIO 成功 → DB 成功 → 完成
        MinIO 成功 → DB 失败 → 回滚删除 MinIO 文件
        MinIO 失败 → 不写 DB → 返回失败

  读取优先级: DB parsed_content → MinIO parsed_content_path（降级）
```

---

## 分词搜索实现

```python
from django.db.models import Q

def _build_tokenized_filter(field_name: str, search_text: str) -> Q:
    """按空格拆分搜索词，构建多词 AND 匹配。
    "量子 计算" → Q(field__icontains="量子") & Q(field__icontains="计算")
    """
    keywords = search_text.strip().split()
    if not keywords:
        return Q()
    q = Q(**{f"{field_name}__icontains": keywords[0]})
    for kw in keywords[1:]:
        q &= Q(**{f"{field_name}__icontains": kw})
    return q
```

---

## 复用的现有函数和模式

| 函数/模式 | 文件 | 说明 |
|-----------|------|------|
| `EmbeddingClient.generate_embedding()` | `apps/memory/services.py:41` | 生成 1024 维向量，复用同一 embedding 模型 |
| `CosineDistance` | `pgvector.django` | 向量相似度搜索 |
| `SearchVector/SearchRank` + `jiebacfg` | `apps/memory/repositories.py:134` | 中文全文检索 |
| `has_active_users()` | `apps/memory/task_helpers.py:9` | GPU 互斥检查 |
| `run_subagent()` | `apps/graph/subagents/base.py:90` | SubAgent 工厂函数 |
| `_get_user_id()` | `apps/graph/subagents/base.py:24` | 从 config 提取 user_id |
| `get_common_tools()` | `apps/graph/subagents/base.py:32` | 公共工具（mem_search + web_search） |
| `acquire_gpu_lock()` | `apps/graph/services/gpu_lock.py` | GPU 锁 |
| `loader.render()` | `apps/context/loader.py:15` | Jinja2 模板渲染 |
| `minio_service.upload_bytes/download_file/delete_file` | `apps/common/storage/minio_service.py` | MinIO 操作 |

---

## SubAgent 路由矩阵

| 用户意图 | 路由 | 示例 |
|---------|------|------|
| 图片/视频/音频分析 | multimodal_subagent | "看看这张图片" |
| 解析新上传文档 | document_subagent | "解析这个 PDF" |
| 查询历史文档 | document_subagent | "之前那篇文档再总结一下" |
| 比较多篇文档 | document_subagent | "前两篇文档综合比较一下" |
| 搜索文档内容 | document_subagent | "哪篇文档提到了量子计算" |
| 列出文档 | document_subagent | "我上传过哪些文档" |

---

## 边界情况处理

| 场景 | 处理 |
|------|------|
| MinIO 写入失败 | 不写 DB，本次结果正常返回，下次重试 |
| DB 写入失败 | 回滚删除已上传的 MinIO 文件 |
| 文件过期 | 原始文件删除，解析结果+embedding 保留可查 |
| 带 `pages` 参数（部分解析）| 跳过缓存（缓存的是全文） |
| 并发解析同一文档 | MinIO 路径+DB attachment_id 固定（幂等） |
| Embedding 生成失败 | `embedding_status='failed'`，定时重试，不影响关键词搜索 |
| 向量搜索异常 | 降级为纯关键词搜索（同 memory 模块） |
| 超大文档解析结果 | PostgreSQL TEXT 无限制，分块时自动切分 |
| 模型版本变更 | 一次性脚本清空 `parsed_content` + 删除 chunk + 重新解析 |

---

## 涉及文件汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `apps/media/models.py` | 修改 | +5 字段 + DocumentChunkEmbedding 新模型 |
| `apps/media/migrations/0002_*.py` | 新增 | 迁移（AddField + CreateModel + GIN 索引） |
| `apps/media/repositories.py` | 修改 | +3 方法 + DocumentChunkEmbeddingRepository |
| `apps/media/services/document.py` | 修改 | +缓存读写 +分块 +RAG 搜索 |
| `apps/context/templates/document_subagent.j2` | **新增** | SubAgent Prompt 模板 |
| `apps/graph/subagents/document_agent.py` | **新增** | 文档 SubAgent + 4 工具 |
| `apps/graph/subagents/multimodal_agent.py` | 修改 | 移除 document_parse |
| `apps/graph/subagents/__init__.py` | 修改 | 注册 document_subagent |
| `core/settings.py` | 修改 | +6 配置项 |
| `apps/media/tasks.py` | 修改 | +embedding 任务 + 重试 + 清理逻辑变更 |
| `core/celery.py` | 修改 | +retry_failed_doc_embeddings Beat |
| `apps/media/serializers.py` | 修改 | +3 只读字段 |
| `apps/media/views.py` | 修改 | 缓存快速返回 |
| `tests/` | 新增/修改 | +20 测试用例 |

---

## 实施顺序

1. 模型 + 迁移 → `makemigrations` + `migrate`
2. Repository 层（MediaAttachmentRepo 新方法 + DocumentChunkEmbeddingRepo）
3. DocumentParseService（缓存读写 + 双写一致 + 分块策略 + RAG 搜索）
4. Celery 任务（embedding 生成 + 定时重试）
5. Jinja2 模板 `document_subagent.j2`
6. `document_agent.py`（4 工具 + SubAgent 入口）
7. `multimodal_agent.py` 精简
8. `__init__.py` 注册
9. `settings.py` + `celery.py`
10. `tasks.py` 清理逻辑变更
11. `serializers.py` + `views.py`
12. 测试 → `pytest`

---

## 测试用例

| 类别 | 测试 | 说明 |
|------|------|------|
| 缓存 | cache_hit | document_parse 命中缓存，跳过 GPU |
| 缓存 | cache_miss | 未命中 → Gateway → 双写 DB+MinIO |
| 缓存 | cache_expired_file | 文件过期但解析结果仍可读 |
| 缓存 | save_minio_fail | MinIO 失败，不写 DB，返回正常 |
| 缓存 | save_db_fail | DB 失败，回滚 MinIO |
| 分块 | chunk_by_heading | Markdown 标题分段 |
| 分块 | chunk_large_paragraph | 超长段落切分 + overlap |
| 分块 | chunk_small_merge | 小段落合并 |
| RAG | keyword_search | 分词关键词搜索 |
| RAG | semantic_search | 向量语义搜索 |
| RAG | hybrid_rerank | 混合检索 + rerank |
| RAG | vector_fallback | 向量异常降级关键词 |
| 工具 | doc_list_sort | 4 种排序 |
| 工具 | doc_list_time_range | 时间范围筛选 |
| 工具 | doc_list_tokenized | 文件名分词搜索 |
| 工具 | doc_read_parsed | 已解析文档读取 |
| 工具 | doc_read_expired | 过期文件解析结果仍可读 |
| 工具 | doc_read_not_parsed | 未解析返回提示 |
| 任务 | embedding_celery | Celery 分块 + embedding |
| 任务 | embedding_retry | 失败重试 |

---

## 验证方式

1. `pytest` 全量通过
2. 上传 PDF → 首次解析 → 双写 DB+MinIO → Celery embedding → 日志确认
3. 再次提问同一文档 → 秒级返回（日志 "Doc cache hit"）
4. 对话"我上传过哪些文档" → doc_list 列表（显示解析状态和原始文件状态）
5. 对话"之前那篇论文的核心观点" → doc_list + doc_read 读取
6. 对话"哪篇文档提到了金融监管" → doc_search(mode=hybrid) 语义+关键词混合搜索
7. 等文件过期后 → doc_read 仍可读取解析结果，doc_search 仍可搜索
8. 检查 PostgreSQL `parsed_content` 和 MinIO `parsed/` 内容一致
9. 检查 `document_chunk_embedding` 表有正确的分块和向量数据
