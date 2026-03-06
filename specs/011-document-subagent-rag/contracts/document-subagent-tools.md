# Contract: Document SubAgent Tools

**Date**: 2026-03-05

## SubAgent 入口工具

### `document_subagent`

```
@tool
async def document_subagent(task: str, config: RunnableConfig) -> str
```

**docstring**: `查询和管理用户的文档。查看文档列表、读取解析内容、搜索文档关键词、解析新文档时使用。`

**行为**:
- 检查 `config.configurable.attachment_uuids`，有附件时注入系统提示到 task
- 调用 `run_subagent(task, config, tools=[doc_list, doc_read, doc_search, document_parse], ...)`
- 使用 `document_subagent.j2` 模板作为 system prompt
- 超时: `DOCUMENT_SUBAGENT_TIMEOUT` (1200s)

---

## 内部工具

### `doc_list` — 文档列表查询

```
@tool
async def doc_list(
    task: str,
    config: RunnableConfig,
    file_name: str = "",
    created_after: str = "",
    created_before: str = "",
    order_by: str = "newest",
    limit: int = 10
) -> str
```

**docstring**: `列出用户的文档附件。支持文件名分词搜索、时间范围筛选和排序。`

**参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| task | str | 必需 | 查询描述 |
| file_name | str | "" | 文件名关键词（空格分隔多词 AND 匹配） |
| created_after | str | "" | YYYY-MM-DD，仅显示此日期之后的文档 |
| created_before | str | "" | YYYY-MM-DD，仅显示此日期之前的文档 |
| order_by | str | "newest" | newest/oldest/name/size |
| limit | int | 10 | 数量上限，最大 20 |

**输出格式**:
```
找到 3 个文档：
1. [abc123] 量子计算研究报告.pdf | 2.3MB | 2026-03-04 10:30 | ✅ 已解析 | 📎 原始文件可用
2. [def456] 金融监管论文.pdf | 1.1MB | 2026-02-20 15:00 | ✅ 已解析 | ⚠️ 原始文件已过期
3. [ghi789] 新上传.docx | 0.5MB | 2026-03-05 09:00 | ❌ 未解析
```

**查询逻辑**:
- 基础过滤: `media_type='document'`, `user_id=user_id`
- **不过滤 `is_expired`**（解析结果在过期后仍可查询）
- file_name 分词: 按空格拆分 → 多个 `Q(file_name__icontains=kw)` AND

---

### `doc_read` — 读取解析结果

```
@tool
async def doc_read(
    attachment_uuid: str,
    config: RunnableConfig,
    max_length: int = 8000
) -> str
```

**docstring**: `读取指定文档的解析结果（Markdown 全文）。`

**参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| attachment_uuid | str | 必需 | 文档附件 UUID（从 doc_list 获取） |
| max_length | int | 8000 | 返回最大字符数 |

**行为**:
- 从 DB `parsed_content` 直接读取
- 降级: DB 为空但 `parsed_content_path` 非空 → 从 MinIO 下载
- 即使原始文件已过期，解析结果仍可读取
- 超过 max_length 截断 + 追加 `[内容已截断，完整内容共 {len} 字符]`
- 未解析返回提示

---

### `doc_search` — 轻量 RAG 检索

```
@tool
async def doc_search(
    query: str,
    config: RunnableConfig,
    mode: str = "hybrid",
    limit: int = 5
) -> str
```

**docstring**: `在用户所有已解析文档中检索内容。支持关键词、语义和混合检索。`

**参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| query | str | 必需 | 搜索查询（自然语言或关键词） |
| mode | str | "hybrid" | keyword/semantic/hybrid |
| limit | int | 5 | 返回片段上限 |

**搜索流程 (hybrid)**:
```
1. query_embedding = EmbeddingClient.generate_embedding(query)
2. vector_results = vector_search(user_id, query_embedding, limit*3)
3. keyword_results = keyword_search(user_id, query, limit*3)
4. rerank:
   - 按 (attachment_id, chunk_index) 聚合去重
   - combined_score = vector_score × 0.7 + keyword_score × 0.3
   - 排序取 top-k
5. 格式化输出
```

**降级**: 向量搜索异常 → 自动降级为纯关键词搜索

**输出格式**:
```
搜索到 3 个相关片段：

1. 📄 量子计算研究报告.pdf [abc123] (相关度: 0.85)
   > 量子计算在金融领域的应用主要集中在...

2. 📄 金融监管论文.pdf [def456] (相关度: 0.72)
   > 监管机构对量子计算技术的关注点包括...
```

---

### `document_parse` — 文档解析（迁移 + 缓存 + 索引）

```
@tool
async def document_parse(
    task: str,
    config: RunnableConfig,
    force: bool = False
) -> str
```

**docstring**: `解析用户上传的 PDF/DOCX 文档。已解析过的文档自动返回缓存结果。设置 force=True 可强制重新解析。`

**参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| task | str | 必需 | 任务描述 |
| force | bool | False | 是否强制重新解析（清除旧缓存） |

**执行流程**:
```
对每个文档附件:
  1. force=True → 清除旧缓存（DB + MinIO + chunk embeddings）
  2. 检查缓存 → 命中则跳过 GPU 锁和 Gateway
  3. 未命中 → GPU 锁 + Gateway 解析
  4. 解析成功 → 双写 DB+MinIO + dispatch Embedding 任务
  5. 结果截断至 DOC_PARSE_MAX_RESULT_LENGTH
```

**缓存命中条件**: `parsed_content IS NOT NULL` 且 `force=False`
**部分解析（带 pages 参数）**: 跳过缓存（缓存的是全文）

---

## Prompt 模板

### `document_subagent.j2`

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
- 用户要求重新解析 → document_parse(force=True)
- 独立完成任务，返回完整结果
```
