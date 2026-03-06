# Tasks: 文档 SubAgent + 解析结果持久化 + 轻量 RAG

**Input**: Design documents from `/specs/011-document-subagent-rag/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — constitution requires service layer 95% coverage, plan specifies 20+ test cases.

**Organization**: Tasks grouped by user story in priority order (P1 → P2). Each story independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks within phase)
- **[Story]**: Which user story this task belongs to (US1–US5 maps to spec.md stories)
- All file paths relative to `backend/`

---

## Phase 1: Setup (Configuration)

**Purpose**: Add feature-specific configuration items

- [x] T001 Add 7 document config items (DOCUMENT_SUBAGENT_TIMEOUT=1200, DOC_CHUNK_SIZE=800, DOC_CHUNK_OVERLAP=100, DOC_VECTOR_WEIGHT=0.7, DOC_KEYWORD_WEIGHT=0.3, DOC_SEARCH_TOP_K=5, DOC_PARSE_MAX_RESULT_LENGTH=6000) to backend/core/settings.py

---

## Phase 2: Foundational (Data Layer)

**Purpose**: Model extensions, migration, repository methods, serializer — BLOCKS all user stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Extend MediaAttachment model with 5 new fields (parsed_content TextField, parsed_content_path CharField(500), parsed_at DateTimeField, parsed_content_size BigIntegerField, embedding_status CharField(20) with choices none/pending/processing/done/failed default='none') and create DocumentChunkEmbedding model with VectorField(dimensions=1024) per data-model.md in backend/apps/media/models.py
- [x] T003 Create migration 0002_add_document_cache_and_chunks.py with pgvector extension CREATE EXTENSION IF NOT EXISTS vector, GIN full-text index on parsed_content with jiebacfg config, and composite indexes per data-model.md in backend/apps/media/migrations/
- [x] T004 [P] Add 5 methods to MediaAttachmentRepository (search_documents with tokenized file_name icontains AND, time range filter, order_by mapping, no is_expired filter; update_parsed_cache atomic 5-field update; update_embedding_status; clear_parsed_cache resetting 5 fields; fulltext_search_parsed_content with SearchVector('parsed_content', config='jiebacfg') + SearchRank for fallback when chunks not indexed) and create DocumentChunkEmbeddingRepository class (bulk_create_chunks, delete_by_attachment_id, vector_search with CosineDistance, keyword_search with SearchVector jiebacfg + SearchRank) per contracts/repository-service-api.md in backend/apps/media/repositories.py
- [x] T005 [P] Add parsed_at, parsed_content_size, embedding_status as read-only fields to MediaAttachmentSerializer in backend/apps/media/serializers.py

**Checkpoint**: Data layer ready — models, migration, repositories, serializer all complete

---

## Phase 3: User Story 5 — 文档 SubAgent 从多模态 SubAgent 独立 (Priority: P1) 🎯 MVP

**Goal**: 文档操作拆分为独立 SubAgent，多模态 SubAgent 仅保留图片/视频/音频分析

**Independent Test**: 分别发送图片分析请求和文档查询请求，验证路由到正确的 SubAgent

- [x] T006 [P] [US5] Create document SubAgent prompt template with tool descriptions (doc_list, doc_read, doc_search, document_parse) and execution strategies (recent doc lookup, multi-doc comparison, content search, parse with cache, force re-parse) per contracts/document-subagent-tools.md prompt section in backend/apps/context/templates/document_subagent.j2
- [x] T007 [US5] Create document_agent.py with document_subagent entry tool (@tool async def, docstring per contract, check config.configurable.attachment_uuids for injection, call run_subagent with tools list and document_subagent.j2 template, timeout=DOCUMENT_SUBAGENT_TIMEOUT) — stub 4 internal tools (doc_list, doc_read, doc_search, document_parse) with placeholder returns in backend/apps/graph/subagents/document_agent.py
- [x] T008 [US5] Register document_subagent by adding import and appending to tools list following existing SubAgent registration pattern (memory/search/code/ha/multimodal) in backend/apps/graph/subagents/__init__.py
- [x] T009 [US5] Remove document_parse tool definition and import from multimodal_agent.py, update MULTIMODAL_PROMPT to explicitly state "仅处理图片、视频、音频分析，不处理文档" and remove document-related instructions in backend/apps/graph/subagents/multimodal_agent.py

**Checkpoint**: SubAgent routing works — document requests go to document_subagent, image/video/audio go to multimodal_subagent

---

## Phase 4: User Story 1 — 文档解析结果缓存复用 (Priority: P1)

**Goal**: 解析结果双写持久化（DB + MinIO），缓存命中秒级返回，支持强制重解析

**Independent Test**: 上传 PDF，首次解析后再次提问同一文档，验证秒级返回且内容一致

- [x] T010 [US1] Implement 3 service methods in DocumentParseService: (1) get_cached_result() — DB parsed_content primary, MinIO parsed_content_path fallback, return None if both empty; (2) save_parsed_result() — MinIO upload to parsed/{user_id}/{date}/{uuid}.md → DB atomic update 5 fields + embedding_status='pending' → dispatch generate_document_embeddings.delay() → compensate on failure per research.md R4; (3) clear_parsed_cache() — delete MinIO file (ignore NotFound) → delete DocumentChunkEmbedding rows → clear DB 5 fields + reset embedding_status='none' per contracts/repository-service-api.md in backend/apps/media/services/document.py
- [x] T011 [P] [US1] Implement document_parse tool replacing stub: for each attachment_uuid from config, (1) if force=True call clear_parsed_cache, (2) check get_cached_result → hit returns cached content, (3) miss → acquire GPU lock → Gateway parse → save_parsed_result, (4) skip cache for partial parse (pages param), (5) truncate result to DOC_PARSE_MAX_RESULT_LENGTH per contracts/document-subagent-tools.md in backend/apps/graph/subagents/document_agent.py
- [x] T012 [P] [US1] Add cache fast-return in parse_document view: when no pages parameter, call DocumentParseService.get_cached_result() → if cached return Response({"cached": True, "content": cached[:max_len], "format": "markdown"}) per contracts/repository-service-api.md views section in backend/apps/media/views.py

**Checkpoint**: Cache pipeline works — first parse dual-writes, second query returns in <1s, force=True clears and re-parses

---

## Phase 5: User Story 2 — 独立文档管理（列出 + 读取）(Priority: P1)

**Goal**: 用户通过自然语言列出文档、按名搜索、按时间筛选、读取完整解析内容

**Independent Test**: 上传若干文档后，通过"我上传过哪些文档"和"之前那篇论文核心观点"验证正确返回

- [x] T013 [US2] Implement doc_list tool replacing stub: call MediaAttachmentRepository.search_documents() with file_name tokenized AND search, created_after/created_before datetime parse, order_by mapping (newest/oldest/name/size), limit capped at 20, format output with attachment_uuid short ID, file_name, file_size, created_at, parsed status icon, is_expired status per contracts/document-subagent-tools.md in backend/apps/graph/subagents/document_agent.py
- [x] T014 [US2] Implement doc_read tool replacing stub: get attachment by uuid + user_id, call get_cached_result() → return content truncated to max_length with "[内容已截断]" suffix if over limit, return "该文档尚未解析" if no cached result, works even when is_expired=True per contracts/document-subagent-tools.md in backend/apps/graph/subagents/document_agent.py

**Checkpoint**: Document management works — users can list, search, filter, and read their documents via natural language

---

## Phase 6: User Story 3 — 文档内容搜索（轻量 RAG）(Priority: P2)

**Goal**: 关键词 + 语义 + 混合检索文档分块内容，自动降级，结果含来源信息

**Independent Test**: 上传多篇文档后搜索特定主题关键词，验证返回相关文档片段且来源标注正确

- [x] T015 [US3] Implement chunk_document() (Markdown heading regex split → paragraph split → merge small chunks up to DOC_CHUNK_SIZE → cut long chunks with DOC_CHUNK_OVERLAP) and search_documents_rag() (keyword mode: repo.keyword_search, semantic mode: EmbeddingClient.generate_embedding + repo.vector_search, hybrid mode: both → rerank by combined_score = vector_score × DOC_VECTOR_WEIGHT + keyword_score × DOC_KEYWORD_WEIGHT → deduplicate by (attachment_id, chunk_index) → top DOC_SEARCH_TOP_K, auto degrade to keyword on vector exception; **fallback**: when chunk-level search returns empty results, degrade to MediaAttachmentRepository.fulltext_search_parsed_content() searching parsed_content GIN index directly — covers US3-AS3 transitional state where parsed_content exists but chunks not yet generated) per contracts/repository-service-api.md in backend/apps/media/services/document.py
- [x] T016 [P] [US3] Create generate_document_embeddings Celery task (@shared_task name="media.generate_document_embeddings"): query attachment → check parsed_content not empty → set embedding_status='processing' → chunk_document() → for each chunk EmbeddingClient.generate_embedding() with GPU mutex has_active_users() → bulk_create_chunks() → set embedding_status='done' or 'failed'; Create retry_failed_doc_embeddings task: scan embedding_status='failed' → re-dispatch; Add retry schedule to Celery Beat (every 5 minutes) per contracts/repository-service-api.md and research.md R7 in backend/apps/media/tasks.py and backend/core/celery.py
- [x] T017 [P] [US3] Implement doc_search tool replacing stub: call search_documents_rag() with mode/limit params, format results with file_name, attachment_uuid, score, chunk_text preview, handle empty results with "未找到匹配内容" message, catch exceptions and degrade gracefully per contracts/document-subagent-tools.md in backend/apps/graph/subagents/document_agent.py
- [x] T018 [US3] Update existing media file expiry cleanup task: when marking attachments is_expired=True, do NOT clear parsed_content, parsed_content_path, parsed_at, parsed_content_size fields; do NOT delete DocumentChunkEmbedding rows; only delete original file from MinIO storage, preserve parsed .md backup in backend/apps/media/tasks.py

**Checkpoint**: RAG search works — keyword, semantic, and hybrid modes return relevant document chunks with source info, auto-degrades on vector service failure

---

## Phase 7: User Story 4 — 多文档综合操作 (Priority: P2)

**Goal**: 支持跨文档比较、汇总多篇文档共同点等综合操作

**Independent Test**: 上传两篇主题相关文档，要求"比较两篇文档异同"，验证输出包含两篇文档的内容分析

- [x] T019 [US4] Verify and enhance document_subagent.j2 prompt template: ensure multi-document comparison strategy is explicit ("分别 doc_read 获取 → 综合比较返回"), add instruction for handling unparsed docs in multi-doc scenarios ("先 document_parse 再 doc_read"), verify synthesis strategy for "汇总多篇文档" requests in backend/apps/context/templates/document_subagent.j2

**Checkpoint**: Multi-document operations work — SubAgent correctly reads multiple docs and synthesizes comparative analysis

---

## Phase 8: Tests & Polish

**Purpose**: Comprehensive test coverage + validation + documentation update

- [x] T020 [P] Create test_document_cache.py: test cache hit returns parsed_content, test cache miss returns None, test MinIO fallback when DB parsed_content empty, test save_parsed_result dual-write success (verify both DB and MinIO), test save_parsed_result DB failure triggers MinIO compensating delete, test save_parsed_result MinIO failure skips DB write, test clear_parsed_cache removes MinIO + chunks + DB fields, test force re-parse clears then re-parses, test expired file still returns cached content, test idempotent concurrent parse same attachment in backend/tests/media/test_document_cache.py
- [x] T021 [P] Create test_document_chunk.py: test Markdown heading split produces correct segments, test paragraph split within sections, test small chunk merging up to DOC_CHUNK_SIZE, test long chunk cutting with DOC_CHUNK_OVERLAP overlap, test empty content returns empty list, test content with no headings falls back to paragraph split, test very long single paragraph is correctly chunked in backend/tests/media/test_document_chunk.py
- [x] T022 [P] Create test_document_rag.py: test keyword search returns ranked results, test semantic search with mock embedding, test hybrid search rerank scoring formula, test hybrid degrade to keyword on vector exception, test empty results return empty list, test user_id isolation (user A cannot see user B docs), test search with no indexed docs returns empty in backend/tests/media/test_document_rag.py
- [x] T023 [P] Create test_document_agent.py: test document_subagent routes correctly (document request → document_subagent), test multimodal_subagent no longer handles documents, test doc_list returns formatted document list, test doc_read returns truncated content, test doc_search returns search results, test document_parse uses cache, test document_parse force=True clears cache in backend/tests/graph/test_document_agent.py
- [x] T024 Run full pytest suite to verify no regressions (expect all existing tests pass + new tests pass)
- [x] T025 Run quickstart.md E2E validation: upload PDF → parse → dual-write verify → cache hit verify → doc_list → doc_read → doc_search(hybrid) → check PostgreSQL parsed_content and MinIO parsed/ consistency → check document_chunk_embedding table
- [x] T026 Update CLAUDE.md: add 011-document-subagent-rag to feature table with status, update Active Technologies section

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US5 (Phase 3)**: Depends on Phase 2 — creates architectural framework
- **US1 (Phase 4)**: Depends on Phase 2 + Phase 3 (document_agent.py must exist)
- **US2 (Phase 5)**: Depends on Phase 2 + Phase 3 (adds tools to document_agent.py)
- **US3 (Phase 6)**: Depends on Phase 2 + Phase 3 + Phase 4 (needs save_parsed_result for embedding dispatch)
- **US4 (Phase 7)**: Depends on Phase 3 + Phase 5 (needs prompt template + doc_read tool)
- **Tests & Polish (Phase 8)**: Depends on all story phases complete

### User Story Dependencies

```
Phase 1: Setup
    ↓
Phase 2: Foundational (BLOCKS ALL)
    ↓
Phase 3: US5 架构拆分 ──────────────────┐
    ↓                                    │
Phase 4: US1 缓存复用 ←─ needs agent.py │
    ↓                                    │
Phase 5: US2 文档管理 ←─ needs agent.py │
    ↓                                    │
Phase 6: US3 RAG 搜索 ←─ needs cache    │
    ↓                                    │
Phase 7: US4 多文档操作 ←─ needs prompt ─┘
    ↓
Phase 8: Tests & Polish
```

### Within Each User Story

- Service layer methods before tool implementations
- Tool implementations before integration
- All tasks in one story complete before checkpoint

### Parallel Opportunities

**Phase 2 (after T002 + T003)**:
```
T004 (repositories.py)  ─┐
                          ├── parallel, different files
T005 (serializers.py)   ─┘
```

**Phase 3**:
```
T006 (document_subagent.j2)  ─┐
                               ├── parallel, different files
T007 (document_agent.py)     ─┘ (then T008 → T009 sequential)
```

**Phase 4 (after T010)**:
```
T011 (document_agent.py)  ─┐
                            ├── parallel, different files
T012 (views.py)           ─┘
```

**Phase 6 (after T015)**:
```
T016 (tasks.py + celery.py)    ─┐
                                 ├── parallel, different files
T017 (document_agent.py)       ─┘
```

**Phase 8**:
```
T020 (test_document_cache.py)   ─┐
T021 (test_document_chunk.py)    ├── all parallel, different files
T022 (test_document_rag.py)      │
T023 (test_document_agent.py)   ─┘
```

---

## Implementation Strategy

### MVP First (US5 + US1 + US2)

1. Complete Phase 1: Setup (config)
2. Complete Phase 2: Foundational (models, migration, repos, serializers)
3. Complete Phase 3: US5 — SubAgent architecture separation
4. Complete Phase 4: US1 — Cache pipeline (dual-write, fast return)
5. Complete Phase 5: US2 — Document list + read tools
6. **STOP and VALIDATE**: Test US5+US1+US2 — upload PDF, parse, cache hit, doc_list, doc_read

### Incremental Delivery

1. Setup + Foundational → Data layer ready
2. Add US5 → SubAgent routing works (MVP architecture)
3. Add US1 → Cache pipeline works (core value)
4. Add US2 → Document management works (user-facing MVP!)
5. Add US3 → RAG search works (enhanced retrieval)
6. Add US4 → Multi-doc operations work (advanced features)
7. Tests + Polish → Production ready

---

## Notes

- All queries MUST filter by `user_id` — constitution mandate, no exceptions
- `is_expired` is NOT filtered in document queries — parsed content outlives original files
- GPU mutex (`has_active_users()`) required for both Gateway parse and Embedding generation
- Dual-write order: MinIO first → DB second → compensate on failure (research.md R4)
- DocumentChunkEmbedding uses `on_delete=CASCADE` from MediaAttachment, but file expiry ≠ deletion
- Embedding dimension = 1024, matching `MEMORY_EMBEDDING_DIMENSION` setting
- Chinese full-text search uses `jiebacfg` PostgreSQL text search configuration
- Commit after each task or logical group
