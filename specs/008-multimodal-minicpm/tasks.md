# Tasks: 全模态模型接入 (MiniCPM-V/o)

**Input**: Design documents from `/specs/008-multimodal-minicpm/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 测试任务已包含，按宪法要求服务层覆盖率 95%+

**Organization**: 任务按用户故事分组，支持独立实现和测试

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 所属用户故事 (US1, US2, US3, US4, US5, US6)
- 包含精确文件路径

## User Story 映射

| 标签 | 用户故事 | 优先级 |
|------|----------|--------|
| US1 | 图像理解对话 | P1 |
| US2 | 中途停止 AI 响应 | P1 |
| US3 | 文档解析与问答 | P2 |
| US4 | 视频内容分析 | P3 |
| US5 | 语音输入与识别 | P4 |
| US6 | AI 语音回复 | P5 |

---

## Phase 1: Setup (基础设施)

**Purpose**: 项目配置和依赖安装

- [x] T001 添加后端 Python 依赖 (Pillow, ffmpeg-python, minio) 到 backend/requirements.txt
- [x] T002 [P] 添加 MinIO 配置到 backend/core/settings.py
- [x] T003 [P] 添加 LLM Gateway 配置到 backend/core/settings.py（多模态模型 ID 映射配置 + 六种超时常量：LLM_GATEWAY_INFERENCE_TIMEOUT=180（消费者：InferenceService T014a）, LLM_GATEWAY_CANCEL_TIMEOUT=5（消费者：InferenceService T014a）, LLM_GATEWAY_POLL_TIMEOUT=30（消费者：DocumentParseService T042a）, LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT=30（消费者：DocumentParseService T042a）, LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT=30（消费者：DocumentParseService T042a）, LLM_GATEWAY_TTS_TIMEOUT=60（消费者：TTSService T059），对应 FR-032 和 upstream-integration-guide.md §8.2；包含 LLM_GATEWAY_API_KEY 环境变量读取；包含 LLM_GATEWAY_DOC_PARSE_MODEL 配置项，默认 minicpm-v；包含 LLM_GATEWAY_GUARDRAILS_LEVEL 配置项，默认 fast；包含 DOC_PARSE_MAX_RESULT_LENGTH 配置项，默认 8000（单位：字符），消费者：T043a 前端文档解析截断逻辑，对应 FR-034）
- [x] T004 [P] 创建 MinIO 初始化脚本 backend/scripts/init_minio.py（初始化脚本中同时配置 MinIO bucket lifecycle rule：media/ 前缀下对象 8 天自动过期删除（比 Celery T065 定时任务多 1 天缓冲），作为 Celery 清理任务的兜底保护，对应 FR-027）
- [x] T005 创建前端媒体类型定义 frontend/src/types/media.ts

---

## Phase 2: Foundational (阻塞性前置任务)

**Purpose**: 所有用户故事依赖的核心基础设施

**⚠️ CRITICAL**: 此阶段完成前，任何用户故事任务不能开始

### 数据模型

- [x] T006 创建 MediaAttachment 模型到 backend/apps/chat/models.py
- [x] T007 生成并应用数据库迁移 (makemigrations + migrate)
- [x] T008 [P] 创建 MediaAttachmentSerializer 到 backend/apps/chat/serializers.py
- [x] T009 [P] 创建 media_attachment_repo 到 backend/apps/chat/repositories.py
- [x] T009a [P] 编写 MediaAttachmentRepository 单元测试 backend/tests/chat/test_media_attachment_repo.py（覆盖：按 UUID 查询、按 user_id 过滤、按 message_id 关联查询、过期记录查询，宪法要求数据仓库层覆盖率 85%+）

### MinIO 存储服务

- [x] T010 创建 MinioService 到 backend/apps/chat/services/minio_service.py（上传/下载/删除/预签名 URL）
- [x] T011 [P] 编写 MinioService 单元测试 backend/tests/chat/test_minio_service.py

### 推理任务管理

- [x] T012 扩展 EventType 枚举添加 INFERENCE_CANCEL 和 DOC_PARSE_PROGRESS 到 backend/apps/common/event_service.py
- [x] T013 创建 InferenceTask 数据类到 backend/apps/chat/services/types.py
- [x] T014 创建 InferenceService 到 backend/apps/chat/services/inference_service.py（注册/查询/取消任务）
- [x] T014a [P] 在 InferenceService 中添加基础 Gateway 调用超时配置（使用 T003 定义的两种超时常量：推理 180s、取消 5s）backend/apps/chat/services/inference_service.py
- [x] T015 [P] 编写 InferenceService 单元测试 backend/tests/chat/test_inference_service.py

### 前端基础组件

- [x] T016 创建 mediaApi.ts 到 frontend/src/services/（上传/获取 API）
- [x] T017 [P] 创建 uploadStore.ts 到 frontend/src/stores/（上传进度状态管理）

**⚠️ BLOCKER**: T009a 为宪法 3.1 合规必要条件（数据仓库层覆盖率 85%+），必须在此 Checkpoint 前完成

**Checkpoint**: 基础设施就绪 - 用户故事实现可以开始

---

## Phase 3: User Story 1 - 图像理解对话 (Priority: P1) 🎯 MVP

**Goal**: 用户上传图片，AI 理解图片内容并回答问题

**Independent Test**: 上传任意图片并提问"这张图片里有什么？"，系统返回准确描述

### 后端实现 (US1)

- [x] T018 [US1] 创建 MediaService 到 backend/apps/chat/services/media_service.py（图片上传处理、格式和大小校验）
- [x] T019 [P] [US1] 编写 MediaService 单元测试 backend/tests/chat/test_media_service.py（必须包含 WebM MIME 类型分类测试：video/webm 归入视频类、audio/webm 归入音频类，验证格式校验和大小校验按正确媒体类型执行）
- [x] T020 [US1] 创建媒体上传视图 upload_media 到 backend/apps/chat/views.py（INVALID_FILE_TYPE 错误响应需包含支持的格式列表，如"支持格式：JPG/PNG/GIF/WebP/MP4/MOV/WebM/WAV/MP3/PDF/DOCX"）
- [x] T021 [P] [US1] 创建媒体获取视图 get_media 到 backend/apps/chat/views.py（文件过期返回 410，非所有者返回 403，对应 FR-030/FR-031）
- [x] T022 [US1] 扩展 ChatRequest 序列化器支持 attachments 参数 backend/apps/chat/serializers.py（attachments 字段类型为 ListField(child=UUIDField())，添加 max_length=5 校验——超过 5 个附件返回 TOO_MANY_ATTACHMENTS 错误码；content 字段添加 max_length=4000 校验——超过 4000 字符返回 CONTENT_TOO_LONG 错误码，对应 multimodal-chat.yaml ChatRequest.content.maxLength: 4000 定义。**类型约束**：attachments 字段仅接受 image/video/audio 类型的 UUID——序列化器 validate_attachments() 中查询 MediaAttachment 并校验 media_type ∈ {image, video, audio}，document 类型由前端通过独立文档解析流程处理不传入此字段，传入 document 类型 UUID 返回 400 INVALID_ATTACHMENT_TYPE。对应 FR-012 和 multimodal-chat.yaml 的 400 响应定义）
- [x] T023 [US1] 扩展 AgentService.execute() 支持多模态消息格式 backend/apps/graph/services/agent_service.py（在创建用户 Message 记录后、调用 LangGraph Agent 前，必须将请求中的 attachment_uuid 列表关联到新创建的 Message：遍历 attachment_uuid → 查询 MediaAttachment → 校验 user_id 所有权和未过期 → 更新 MediaAttachment.message_id = message.id。关联失败时整个请求回滚）
- [x] T024 [US1] 创建多模态消息构建器 build_multimodal_messages() 到 backend/apps/graph/agent.py（实现 FR-025 内容类型→模型 ID 路由映射：纯文本→默认模型、图片/视频→minicpm-v、音频→minicpm-o；混合媒体优先级规则：当附件同时包含音频和图片/视频时，统一使用 minicpm-o（音频优先级最高），使用 T003 定义的模型 ID 配置。**Gateway 护栏参数**：build_multimodal_messages() 返回的请求参数中包含 `guardrails_level` 字段，值从 `settings.LLM_GATEWAY_GUARDRAILS_LEVEL`（默认 `fast`）读取，对应 spec.md Gateway 护栏参数说明）
- [x] T025 [US1] 添加媒体 URL 路由到 backend/apps/chat/urls.py
- [x] T026 [P] [US1] 编写媒体上传 API 集成测试 backend/tests/chat/test_media_views.py（必须包含：非所有者访问媒体文件返回 403（FR-031）、已过期文件返回 410（FR-030）、文档大小超限返回 400（FR-014a）、**发送消息携带超过 5 个附件返回 400 TOO_MANY_ATTACHMENTS（FR-012）** 的测试用例）
- [x] T068 [US1] 集成 Langfuse 追踪多模态推理 backend/apps/graph/services/agent_service.py（验收标准：所有类型的多模态推理（图片/视频/音频/混合媒体）在 Langfuse 面板可查看 trace，包含 model、media_types、attachment_count、duration span，以及 Gateway X-Request-ID 关联。验证方法：上传图片发送消息后，Langfuse 中可见对应 trace 且 media_types=["image"]。对应 FR-033。从 Phase 9 提前至此——FR-033 为必须需求，MVP 部署即需可观测性）

### 前端实现 (US1)

- [x] T027 [P] [US1] 创建 MediaUploader 组件 frontend/src/components/chat/MediaUploader.tsx（支持多文件选择≤5个（**总选择数上限含所有媒体类型 image/video/audio/document**，对应 FR-012 定义；发送时前端按 media_type 分流——image/video/audio 的 UUID 放入 chatRequest.attachments，document 走独立解析流程，详见 T031/T043a）、批量预览、逐个上传进度、前端格式和大小双重校验：格式校验不通过时提示支持的格式列表，大小校验按媒体类型检查上限（图片≤10MB、视频≤50MB、音频≤10MB、文档≤10MB），与后端双重校验保证数据合法性。WebM MIME 类型分类：video/webm 归入视频类、audio/webm 归入音频类，前端 accept 属性和大小校验需按此区分。**音频文件时长校验**：用户选择已有音频文件（非 AudioRecorder 录音）时，通过 HTML5 Audio 元素 loadedmetadata 事件读取 duration，低于 1 秒提示"音频时长过短（最短 1 秒）"阻止上传，超过 60 秒提示"音频时长不能超过 60 秒"阻止上传，对应 spec.md Edge Case 和 data-model.md 4.1 验证规则）
- [x] T028 [P] [US1] 创建 MediaPreview 组件 frontend/src/components/chat/MediaPreview.tsx（按媒体类型显示对应静态 SVG 占位图；**未过期文件加载**：图片类型通过 `<img src={GET /api/v1/chat/media/{uuid}/}>` 直接渲染原始图片（需携带 cookie 认证），视频类型通过 `<video>` 标签加载，音频通过 AudioPlayer 组件播放；点击已过期媒体时处理后端 HTTP 410 响应，展示"文件已过期"提示，对应 FR-028/FR-030）。含创建 4 个 SVG 占位图资源 frontend/src/assets/placeholders/（image-placeholder.svg、video-placeholder.svg、audio-placeholder.svg、document-placeholder.svg）
- [x] T029 [US1] 扩展 MessageInput 组件支持图片上传按钮 frontend/src/components/chat/MessageInput.tsx
- [x] T030 [US1] 扩展 MessageList 组件渲染带附件的消息 frontend/src/components/chat/MessageList.tsx
- [x] T031 [US1] 扩展 chatApi 发送消息时携带 attachments 参数 frontend/src/services/chatApi.ts（按 media_type 路由：image/video/audio→多模态推理，document→文档解析流程（详见 T043a）。含前端格式和大小前置校验）

- [x] T074a [P] 创建 backend/apps/chat/services/CLAUDE.md（覆盖 media_service、minio_service、inference_service 模块说明，宪法第七条要求）

**Checkpoint**: 图像理解对话功能完整可用，可独立测试和演示

---

## Phase 3.5: 媒体过期清理 (阻塞性运维保障)

**Purpose**: MVP 部署后即产生媒体文件，必须在 7 天窗口期内建立清理机制

- [x] T065 创建 Celery 定时任务清理过期媒体文件 backend/apps/chat/tasks.py（实现为 Celery Task，由 T066 Celery Beat 调度。清理逻辑：1) 查询 expires_at < now 且 is_expired=False 的 MediaAttachment 记录；2) 逐条删除 MinIO 中对应的原始文件；3) 删除成功后更新 is_expired=True；4) 记录清理日志。**失败补偿**（宪法 1.3）：单条 MinIO 删除失败时记录 ERROR 日志并跳过（不阻塞后续记录），is_expired 保持 False 下次定时任务自动重试；连续 10 条失败时发出 CRITICAL 告警并终止本轮清理（疑似 MinIO 不可达））
- [x] T066 配置 Celery Beat 调度（每日凌晨 3 点）backend/core/celery.py（在现有 beat_schedule 配置中添加 T065 的媒体清理 Celery Task）
- [x] T066a [P] 编写 Celery 媒体清理任务单元测试 backend/tests/chat/test_media_cleanup_task.py（覆盖：过期记录查询逻辑、MinIO 删除调用验证、is_expired 标记更新、空结果集处理、**单条 MinIO 删除失败时跳过并保持 is_expired=False**、**连续 10 条失败时终止本轮并记录 CRITICAL**、**清理后通过 GET /api/v1/chat/media/{uuid}/ 获取已过期文件返回 HTTP 410 的端到端验证**，宪法要求服务层覆盖率 95%+）

**Checkpoint**: 媒体文件自动清理机制就绪

---

## Phase 4: User Story 2 - 中途停止 AI 响应 (Priority: P1)

**Goal**: 用户可随时点击停止按钮终止推理，500ms 内生效

**Independent Test**: AI 回答过程中点击停止按钮，AI 立即停止输出

### 后端实现 (US2)

- [x] T032 [US2] 扩展 InferenceService 实现 cancel_inference() 调用网关中断接口 backend/apps/chat/services/inference_service.py（**状态清理时序**：1) 删除 Redis `user:{user_id}:inference_task` 键；2) 发布 INFERENCE_CANCEL 事件；3) 并行调用 Gateway `/v1/chat/cancel`（可选，超时降级）。步骤 1 必须在步骤 2 之前完成，确保 AgentService 收到中断信号时 InferenceTask 已不存在，新请求可立即创建新任务）
- [x] T033 [US2] 创建推理取消视图 cancel_inference 到 backend/apps/chat/views.py
- [x] ~~T034~~ [REMOVED] 宪法 9.2
- [x] T035 [US2] 扩展 AgentService 支持推理取消信号处理 backend/apps/graph/services/agent_service.py（SSE 生成器中通过 async Redis Pub/Sub 监听 INFERENCE_CANCEL 事件，收到时发送 `interrupted` SSE 消息并退出循环。同时处理 Gateway `content_control` 事件（护栏触发时丢弃内容，发送 SSE error）。**Pub/Sub 降级策略**：Pub/Sub 连接异常时（如 Redis 订阅失败），降级为 fallback 轮询——SSE 生成循环每次迭代中检查 Redis `user:{user_id}:inference_task` 键是否存在（GET 操作），键不存在说明已被 T032 cancel_inference() 步骤 1 删除，即视为取消信号，发送 `interrupted` 并退出。轮询间隔与 Pub/Sub 超时对齐（1 秒），不增加额外延迟。此降级仅在 Pub/Sub 不可用时启用，不阻塞正常推理。**取消完成后必须清理 Redis InferenceTask 键**，确保取消后用户立即发送新消息时不会残留旧任务状态，对应 spec.md 边界情况"推理取消后用户立即发送新消息"）
- [x] T036 [US2] 添加推理控制 URL 路由到 backend/apps/chat/urls.py
- [x] T037 [P] [US2] 编写推理取消 API 集成测试 backend/tests/chat/test_inference_cancel.py（必须覆盖：取消成功返回 200、无活跃任务返回 404、指定 request_id 取消、取消后立即发送新请求成功、Gateway 取消接口超时处理、**Redis Pub/Sub 连接异常时降级为 fallback 轮询——mock Redis subscribe 抛出异常，验证 AgentService 切换为每秒 GET user:{user_id}:inference_task 键检查取消信号，取消后仍能在 1 秒内发送 interrupted SSE 事件**（对应 T035 Pub/Sub 降级策略）、**Gateway 流式响应中收到 content_control SSE 事件时 AgentService 丢弃已缓冲内容并以 replacement 文本通过 SSE error 事件推送给前端**（mock Gateway SSE 流返回 content_control 事件，验证收到 error 类型 SSE 且 content 为 replacement 文本，对应 spec.md Gateway 护栏参数说明）。注：语音打断 interrupt_playback 不触发取消接口的负向验证为前端行为，在 T073a E2E 测试中覆盖）

### 前端实现 (US2)

- [x] ~~T038~~ [REMOVED] 合并至 T016
- [x] T039 [US2] 扩展 MessageInput 停止按钮调用 mediaApi 中的推理取消 API frontend/src/components/chat/MessageInput.tsx（停止按钮需添加 500ms 防抖，防止快速连续点击重复调用取消接口）
- [x] ~~T040~~ [REMOVED] 宪法 9.2
- [x] ~~T041~~ [REMOVED] 宪法 9.2

**Checkpoint**: 推理取消功能完整可用，500ms 内生效

---

## Phase 5: User Story 3 - 文档解析与问答 (Priority: P2)

**Goal**: 用户上传 PDF 截图或文档图片，AI 解析并回答问题

**Independent Test**: 上传包含表格的 PDF 截图，提问表格内容，系统准确返回数据

### 后端实现 (US3)

- [x] T042a [US3] 创建 DocumentParseService 到 backend/apps/chat/services/document_parse_service.py（三阶段异步工作流：从 MinIO 下载原始文件 → 上传至 Gateway POST /v1/documents/parse（model 参数必填，从 settings.LLM_GATEWAY_DOC_PARSE_MODEL 读取，默认 minicpm-v）→ 轮询 GET /v1/documents/tasks/{task_id} → 获取结果 GET .../result?format=markdown；轮询过程中通过 EventService 发布 DOC_PARSE_PROGRESS 事件推送 current/total 页数进度到前端；需处理 Gateway 错误码 E6001-E6009 和 E3001/E3002，参见 docs/multimodal-api-guide.md 第六节和 data-model.md 2.6 节。支持可选 pages 参数透传至 Gateway POST /v1/documents/parse 请求，语法同 Gateway 如 "1,3-5,8"，不传则解析全部页。创建解析任务成功后写入 Redis 所有权键 `doc_parse:{task_id}:owner` = user_id，TTL 7 天，供 T075 状态/结果查询视图校验所有权。**运行机制**：`_poll_and_notify()` 后台轮询通过 `asyncio.create_task()` 在当前请求协程中启动（非 Celery Task），利用 ASGI 事件循环原生支持——视图返回 HTTP 202 后轮询在后台继续执行直至完成或超时）
- [x] ~~T042~~ [REMOVED] SC-003 已移除
- [x] T042b [P] [US3] 编写 DocumentParseService 单元测试 backend/tests/chat/test_document_parse_service.py（必须覆盖：创建解析任务、轮询状态、获取结果、**创建任务使用 LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT=30s 超时**、**轮询使用 LLM_GATEWAY_POLL_TIMEOUT=30s 超时**、Gateway 错误格式映射验证、E6001-E6009 和 E3001/E3002 错误响应处理，宪法要求服务层覆盖率 95%+）
- [x] ~~T044~~ [REMOVED] 合并至 T024 和 T078
- [x] T075 [US3] 创建文档解析视图（create/status/result 三个视图）到 backend/apps/chat/views.py（**两层权限校验**：create 视图校验附件所有权（403 → ATTACHMENT_ACCESS_DENIED），status/result 视图校验任务所有权（Redis `doc_parse:{task_id}:owner`，403 → TASK_ACCESS_DENIED）。TTL 7 天。**所有权键过期后行为**：Redis `doc_parse:{task_id}:owner` 键 TTL 7 天过期后，GET 查询返回 404（任务不存在），与 Gateway 侧 7 天自动清理对齐——Gateway 返回 E6009 时 LinChat 映射为 410 TASK_EXPIRED（contracts/document-parse.yaml 已定义），两端过期窗口一致。错误映射参见 contracts/document-parse.yaml）
- [x] T076 [US3] 添加文档解析 URL 路由到 backend/apps/chat/urls.py（/documents/parse/, /documents/tasks/{id}/, /documents/tasks/{id}/result/）
- [x] T077 [P] [US3] 编写文档解析 API 集成测试 backend/tests/chat/test_document_parse_views.py（覆盖 contracts/document-parse.yaml 定义的所有响应码，含所有权校验和 Gateway 错误映射，覆盖率 80%+）

### 前端实现 (US3)

- [x] T043 [P] [US3] 扩展 MediaPreview 组件支持文档类型图标显示 frontend/src/components/chat/MediaPreview.tsx
- [x] T043a [US3] 实现前端文档解析交互流程 frontend/src/components/chat/MessageList.tsx + frontend/src/services/chatApi.ts + frontend/src/hooks/useDocParse.ts + frontend/src/hooks/useAuth.tsx（document 类型附件→创建解析任务→通过自定义 Hook `useDocParse` 订阅 SSE DOC_PARSE_PROGRESS 事件→获取 Markdown 结果→组合用户问题发送纯文本聊天。**SSE 通道说明**：DOC_PARSE_PROGRESS 事件通过 `GET /api/v1/events` 长连接推送（非 POST /api/v1/chat/ 聊天流——文档解析发生在推理之前，聊天 SSE 流此时不存在）。后端 DocumentParseService 通过 `EventService.publish_event(user_id, "doc_parse_progress", {...})` 发布到 Redis Pub/Sub 频道 `events:user:{user_id}`，EventsView 转发给前端。前端实现：在 `useAuth.tsx` 事件分发逻辑中新增 `doc_parse_progress` 事件类型处理，通过 `window.dispatchEvent(new CustomEvent('doc_parse_progress', { detail: data }))` 分发（与现有 `context_status` 事件相同模式）；`useDocParse` Hook 通过 `addEventListener('doc_parse_progress', ...)` 接收并更新解析进度 UI：pending→"等待解析"、processing→"解析中 X/Y 页"、completed→自动获取结果、failed→展示错误。超过 DOC_PARSE_MAX_RESULT_LENGTH（T003 定义，默认 8000）字符截断，对应 FR-034。错误处理参见 contracts/document-parse.yaml。文档页数限制错误展示：Gateway 返回 E6006 PAGE_LIMIT_EXCEEDED 时，前端显示友好提示"文档页数超过限制（最大 200 页），请使用 pages 参数指定范围或上传更短文档"，E6006 PAGE_LIMIT_EXCEEDED 时前端显示错误提示并提供文本输入框让用户填入页码范围（placeholder："输入页码范围，如 1-50"），点击"重新解析"按钮以新 pages 参数重新调用 POST /api/v1/chat/documents/parse/。混合附件（document + image/video/audio）解析失败时，展示失败原因并提供选择：移除文档仅发送剩余附件 或 取消整条消息，对应 FR-012 混合附件解析失败处理规则）

- [x] T074b [P] 更新 backend/apps/chat/services/CLAUDE.md（追加 document_parse_service 模块说明，宪法第七条要求）

**Checkpoint**: 文档解析问答功能完整可用

---

## Phase 6: User Story 4 - 视频内容分析 (Priority: P3)

**Goal**: 用户上传短视频（≤60秒），AI 理解视频内容并回答问题

**Independent Test**: 上传 10 秒视频并提问"视频里发生了什么？"，系统返回视频描述

### 后端实现 (US4)

- [x] T045 [US4] 扩展 MediaService 支持视频上传处理（格式校验、大小校验 ≤50MB）backend/apps/chat/services/media_service.py
- [x] T046 [US4] 扩展 MediaService 支持视频时长检测和验证 backend/apps/chat/services/media_service.py（依赖 T045）
- [x] T047 [US4] 扩展 build_multimodal_messages() 支持视频消息格式 backend/apps/graph/agent.py
- [x] T048 [P] [US4] 编写视频处理单元测试 backend/tests/chat/test_video_processing.py（必须包含：视频附件推理后 Langfuse trace 中 media_types 含 "video"、model 为 minicpm-v 的 span 验证，对应 FR-033 可观测性要求）

### 前端实现 (US4)

- [x] T049 [P] [US4] 扩展 MediaUploader 支持视频文件选择和预览 frontend/src/components/chat/MediaUploader.tsx（含前端视频时长预检：通过 HTML5 Video 元素 loadedmetadata 事件读取 duration，超过 60 秒时阻止上传并提示"视频时长不能超过 60 秒"，对应 spec.md FR-015 前端校验层）
- [x] T050 [US4] 扩展 MediaPreview 支持视频播放器 frontend/src/components/chat/MediaPreview.tsx
- [x] T051 [US4] 添加视频上传进度分阶段显示 frontend/src/components/chat/MediaUploader.tsx（阶段 1"上传中 X%"：通过 XMLHttpRequest onprogress 回调显示百分比进度条；阶段 2"准备就绪"：上传 API 返回成功后切换为完成状态图标，表示文件已存入 MinIO 可供发送。注：视频无独立后端预处理阶段，上传完成即可发送）

- [x] T051a [P] [US4] 添加视频推理超时前端提示 frontend/src/components/chat/MessageList.tsx（发送含视频附件消息后启动本地计时器，若 SSE 首个 content 块超过附件 duration_seconds × 2 秒未到达，显示"AI 正在分析视频，请耐心等待..."提示，收到首个 content 后清除计时器，对应 US4-AC3 和 SC-005）

**Checkpoint**: 视频内容分析功能完整可用

---

## Phase 7: User Story 5 - 语音输入与识别 (Priority: P4)

**Goal**: 用户通过语音输入与 AI 对话，系统将语音转换为文字

**Independent Test**: 录制 5 秒语音"今天天气怎么样？"，系统识别并返回文字回复

### 后端实现 (US5)

- [x] T052 [US5] 扩展 MediaService 支持音频上传 backend/apps/chat/services/media_service.py（音频时长 < 1 秒时返回 DURATION_TOO_SHORT 错误，对应边界情况"语音录制时间过短"）
- [x] T053 [US5] 扩展 build_multimodal_messages() 支持音频消息格式（使用 minicpm-o）backend/apps/graph/agent.py（minicpm-o 接收音频后返回理解性文字回复，非独立 ASR 转写；回复通过现有 SSE content 流传输，前端无需额外 ASR 展示逻辑，对应 FR-019。占位文本处理：**仅当消息同时携带 audio 类型附件时**，若 content 为"[语音消息]"则构建模型消息时替换为空字符串仅传音频，若有用户追加文字则保留文本与音频一同传入；无 audio 附件时即使 content 恰好为"[语音消息]"也保留原文（防止用户文本误匹配），对应 spec.md US5-AC3）
- [x] T054 [P] [US5] 编写音频处理单元测试 backend/tests/chat/test_audio_processing.py（必须包含：WebM/WAV/MP3 三种格式的上传验证、video/webm 与 audio/webm 的 MIME type 区分测试、build_multimodal_messages() 对"[语音消息]"占位文本的替换逻辑验证——**仅当携带 audio 附件时** content 为"[语音消息]"替换为空字符串仅传音频、用户追加文字时保留文本与音频一同传入、**无 audio 附件时 content 为"[语音消息]"保留原文不替换**（负向测试，防止用户文本误匹配），对应 spec.md US5-AC3。补充验证：音频附件推理后 Langfuse trace 中 media_types 含 "audio"、model 为 minicpm-o 的 span 验证，对应 FR-033 可观测性要求）

### 前端实现 (US5)

- [x] T062 [P] [US5/US6] 创建 AudioPlayer 组件 frontend/src/components/chat/AudioPlayer.tsx（播放/暂停/进度/打断。暴露 stopAndClear() 方法供外部调用，打断时清空音频播放队列并重置播放状态，打断按钮复用 500ms 防抖策略，对应 FR-009。注：打断仅停止前端 TTS 播放，不联动后端推理取消——因为 TTS 是独立的音频合成请求，推理此时已完成）
- [x] T055 [P] [US5] 创建 AudioRecorder 组件 frontend/src/components/chat/AudioRecorder.tsx（录音/停止/预览，录音时长校验：最短 1 秒、最长 60 秒，低于 1 秒时提示"录音时间过短"阻止发送，对应 spec.md 边界情况和 data-model.md 4.1 验证规则）
- [x] T056 [US5] 创建 useAudioRecorder Hook frontend/src/hooks/useAudioRecorder.ts
- [x] T057 [US5] 扩展 MessageInput 集成语音录制按钮 frontend/src/components/chat/MessageInput.tsx（录音完成后语音文件作为附件，同时将预览文本"[语音消息]"填入输入框供用户编辑后发送，对应 US5-AC3）
- [x] T058 [US5] 扩展 MessageList 渲染语音消息（显示播放按钮，对应 FR-021a）frontend/src/components/chat/MessageList.tsx（依赖 T062 AudioPlayer 组件）

**Checkpoint**: 语音输入与识别功能完整可用

---

## Phase 8: User Story 6 - AI 语音回复 (Priority: P5)

**Goal**: AI 回复可转换为语音播放

**Independent Test**: 发送文字消息，点击回复的"播放语音"按钮，听到语音朗读

### 后端实现 (US6)

- [x] T059 [US6] 创建 TTS 服务调用网关 TTS 接口 backend/apps/chat/services/tts_service.py（接收 message_uuid，通过 Repository 查询 Message.content 文本，校验 role=assistant 和 user_id 所有权，文本超 2000 字符时拒绝合成，然后调用 Gateway `/v1/audio/speech` 接口返回音频流。**超时配置**：使用 T003 定义的 LLM_GATEWAY_TTS_TIMEOUT=60s，对应 FR-032。**Gateway E3002 错误区分规则**：E3002 响应包含 retry_after 字段时返回 TTS_MODEL_SWITCHING（含 estimated_wait_seconds），不含 retry_after 时返回 TTS_SERVICE_UNAVAILABLE，对应 contracts/tts.yaml 两种 503 响应定义）
- [x] T059a [P] [US6] 编写 TTS Service 单元测试 backend/tests/chat/test_tts_service.py（必须覆盖：正常合成、文本过长拒绝、Gateway 超时、模型不存在 E3001→404、**E3002 双路径区分——有 retry_after 字段时返回 TTS_MODEL_SWITCHING（含 estimated_wait_seconds）、无 retry_after 时返回 TTS_SERVICE_UNAVAILABLE**（对应 contracts/tts.yaml 两种 503 响应定义和 T059 区分规则），宪法要求服务层覆盖率 95%+）
- [x] T060 [P] [US6] 创建 TTS 视图 get_tts_audio backend/apps/chat/views.py
- [x] T061 [US6] 添加 TTS URL 路由 backend/apps/chat/urls.py
- [x] T061a [P] [US6] 编写 TTS 视图集成测试 backend/tests/chat/test_tts_views.py（覆盖：正常合成返回音频流、文本超 2000 字符拒绝、非 assistant 消息拒绝、消息所有权校验、TTS 服务不可用 503，宪法要求 API 视图层覆盖率 80%+）

### 前端实现 (US6)

- [x] T063 [US6] 扩展 MessageList 在 AI 回复消息添加播放语音按钮 frontend/src/components/chat/MessageList.tsx（AI 回复文本超过 2000 字符时，TTS 按钮置灰并显示 tooltip"文本过长，暂不支持语音播放"，对应 FR-020a）
- [x] T064 [US6] 创建 ttsApi.ts 到 frontend/src/services/（TTS API 调用，错误处理参见 contracts/tts.yaml）

- [x] T074c [P] 更新 backend/apps/chat/services/CLAUDE.md（追加 tts_service 模块说明，宪法第七条要求）
- [x] T074d [P] 创建 backend/apps/chat/CLAUDE.md + backend/apps/common/CLAUDE.md + backend/apps/graph/CLAUDE.md（宪法第七条要求）

**Checkpoint**: AI 语音回复功能完整可用

---

## Phase 9: Polish & 跨功能优化

**Purpose**: 跨用户故事的改进和收尾工作

### 容错处理

- [x] T067 增强网关容错 backend/apps/common/gateway_utils.py + backend/apps/chat/services/{inference_service,document_parse_service,tts_service}.py（**首先创建** backend/apps/common/gateway_utils.py 共享工具模块，然后在三个 Service 中引用。职责范围：InferenceService/DocumentParseService/TTSService 层的 httpx 异常捕获（ConnectionError→LLMConnectionError、TimeoutError→LLMTimeoutError）+ Gateway JSON 错误响应解析（{"error":{"code":"Exxxx"}}→LinChat 异常类映射）+ X-Request-ID 请求头透传 + 所有 Gateway 调用（含 cancel 请求）记录 Langfuse span（model、request_type、duration、status_code），补全 FR-033 cancel 接口的可观测性覆盖。异常映射参见 plan.md Constitution Check 4.3。注：SSE 流内 Gateway 错误事件（content_control 等）的处理由 T035/T079 在 AgentService 层负责。**重试实现方式**（宪法 4.3）：三个 Service 共用统一 retry 装饰器（基于 tenacity 库实现，依赖已在 T001 requirements.txt 中添加），配置 `@gateway_retry(max_retries=3, retry_on=(LLMConnectionError, LLMTimeoutError))`，放置在 backend/apps/common/gateway_utils.py 中供三个 Service 引用。LLMRateLimitError/LLMContentFilterError/LLMContextLengthError/LLMQuotaExceededError 不重试，直接向上抛出。**请求头注入**：三个 Service 统一使用 gateway_utils.py 提供的 `build_gateway_headers(request_id)` 函数注入 `Authorization: Bearer {LLM_GATEWAY_API_KEY}` 和 `X-Request-ID: {request_id}` 两个请求头，避免各 Service 重复实现。**测试要求**：gateway_utils.py 的单元测试须覆盖 Langfuse span 记录验证——mock Langfuse client，调用各 Gateway 包装函数后断言 span 被创建且包含 model/request_type/duration/status_code 字段，含 cancel 请求场景，对应 FR-033 和宪法 3.1 工具函数覆盖率 90%+）
- [x] T067a [P] 前端统一处理网关错误和超时 frontend/src/components/chat/MessageList.tsx（E3001/E3002→"服务暂不可用"、504→"响应超时"。**模型切换等待交互**：E3002 响应含 retry_after 字段时，前端显示倒计时提示"模型切换中，约 N 秒后可重试"（N 从 retry_after 值递减），倒计时结束后显示可点击的"重试"按钮，点击后重新发送原始请求。对应 FR-032 和 spec.md 模型互斥约束）

### 可观测性

- [x] ~~T068~~ [MOVED] 移至 Phase 3
- [x] T068a [P] 集成 Langfuse 追踪 TTS 和文档解析 Gateway 请求 backend/apps/chat/services/tts_service.py + document_parse_service.py（每次 Gateway 调用记录 Langfuse span，包含 model、request_type、duration，对应 FR-033）
- [x] T069 [P] 添加媒体上传操作 Python 日志（INFO 级别）backend/apps/chat/services/media_service.py（注：FR-033 "多模态推理"Langfuse 追踪由 T068/T068a 覆盖，媒体上传操作不属于推理范畴，使用标准 Python logging 记录上传事件即可，含：上传成功/失败、文件格式校验结果、文件大小信息）

### 文档与配置

- [x] T070 [P] 更新 Nginx 配置支持大文件上传 (client_max_body_size 60m)，同时确认 MinIO 和 Docker 环境的上传大小限制配置（MinIO 默认支持 5GB 单文件无需调整，确认 docker-compose.yml 无额外限制）。同步更新 CLAUDE.md 服务启动顺序，新增 Celery Worker 和 Beat 启动命令：`celery -A core worker --loglevel=info` 和 `celery -A core beat --loglevel=info`（T065/T066 引入的媒体过期清理定时任务依赖 Celery 运行）
- [x] T071 [P] 运行 quickstart.md 验证所有功能
- [x] ~~T074~~ [DISTRIBUTED] 后端模块文档已分布至 T074a（Phase 3）、T074b（Phase 5）、T074c/T074d（Phase 8）

### 模型路由与容错

- [x] T078 [P] [US1] 编写模型路由逻辑单元测试 backend/tests/chat/test_model_routing.py（验证内容类型→模型ID映射：纯文本→默认模型，图片/视频→minicpm-v，音频→minicpm-o，混合媒体（图片+音频）→minicpm-o 音频优先，文档类型不经过 build_multimodal_messages()（原 T044 验证项））
- [x] T079 [P] AgentService SSE 流内 Gateway 模型错误处理 backend/apps/graph/services/agent_service.py（职责范围：SSE 流消费过程中遇到 Gateway 错误事件时，转换为 LinChat SSE error/interrupted 事件推送给前端。映射规则：E3001 模型不存在→SSE error "请求的模型不存在"，E3002 模型不可用→SSE error "多模态服务暂时不可用，请稍后重试"含 retry_after。与 T067 httpx 层异常处理互补：T067 处理请求级异常，T079 处理 SSE 流内事件级错误。注：FR-003 的"可用模型列表"由 Gateway 侧返回，LinChat 不解析该列表）

### 前端模块文档

- [x] T080 [P] 创建前端模块 README（components/chat、hooks、stores、services、types 五个目录，宪法第七条要求）

### 前端组件单元测试

- [x] T081 [P] 编写 MediaUploader 组件单元测试 frontend/tests/components/chat/MediaUploader.test.tsx（覆盖：文件选择、格式校验、大小校验、多文件≤5、上传进度显示）
- [x] T082 [P] 编写 MediaPreview 组件单元测试 frontend/tests/components/chat/MediaPreview.test.tsx（覆盖：图片/视频/音频/文档类型渲染、静态占位图显示、过期文件点击提示）
- [x] T083 [P] 编写 AudioRecorder 组件单元测试 frontend/tests/components/chat/AudioRecorder.test.tsx（覆盖：开始/停止录音、时长限制、最短1秒校验、格式输出）
- [x] T084 [P] 编写 AudioPlayer 组件单元测试 frontend/tests/components/chat/AudioPlayer.test.tsx（覆盖：播放/暂停、进度条、打断清理）
- [x] T085 [P] 编写 mediaApi.ts / ttsApi.ts / 文档解析截断逻辑 / useDocParse Hook 单元测试 frontend/tests/services/mediaApi.test.ts + frontend/tests/hooks/useDocParse.test.ts（覆盖：上传请求、取消推理、TTS 调用、错误处理、FR-034 文档解析结果截断逻辑——验证超过 DOC_PARSE_MAX_RESULT_LENGTH（默认 8000）字符时截取并追加截断提示、恰好 8000 字符时不截断、空结果处理、**组合文本格式验证——确认最终发送格式为 `[文档内容]\n{markdown}\n[/文档内容]\n\n{user_question}`**。追加 useDocParse Hook 单元测试：覆盖 SSE doc_parse_progress 事件接收与状态流转 pending→processing→completed/failed、completed 后自动获取结果、failed 后展示错误信息、超时处理）

### 上线后验收

- [ ] T086 [P] 上线后 30 天内通过 Langfuse trace 统计：1) 首次用户成功率（SC-007 ≥ 80%，筛选条件：首次包含 media_types 的 trace 且结果非 error 类型）；2) 多模态服务可用性（SC-006 > 99%，筛选条件：月度滚动 30 天内 trace success_rate，排除 interrupted 和维护窗口）；3) 配置 Langfuse Dashboard 面板展示上述指标趋势图。评估结果记录到 docs/ 目录

### 端到端测试

- [x] T072 [P] E2E 测试：图片上传 + AI 问答完整流程 frontend/tests/e2e/multimodal-image.spec.ts
- [x] T073 [P] E2E 测试：推理取消流程（发送 → 停止 → 重新发送）frontend/tests/e2e/inference-cancel.spec.ts
- [x] T073a [P] E2E 测试：语音交互流程（录音发送 → AI 文字回复 → 点击 TTS 播放 → 点击打断 → 再次录音发送）frontend/tests/e2e/voice-interaction.spec.ts（覆盖 FR-009/FR-021 半双工模式，验证打断后 AudioPlayer.stopAndClear() 正确清理队列且后续录音正常。**负向验证**：打断按钮点击后网络层未发出 POST /api/v1/chat/inference/cancel/ 请求（interrupt_playback 仅停止前端播放，不联动后端），对应 spec.md US2 术语说明中 interrupt_playback 与 cancel_inference 的职责区分）

---

## Notes

- [P] 任务 = 不同文件，无依赖，可并行
- 执行顺序、Phase 依赖关系、并行策略参见 plan.md
- 每个 Checkpoint 处可独立验证用户故事
