# Apps 模块总览

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## App 列表

| App | 关键模型 | 说明 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式响应（ASGI 异步）、生成控制（停止/恢复/重连）、历史消息排除限流 |
| `common` | 无 | Token 中间件、WebSocket 认证、异常体系、响应格式、SSE 事件（Redis Pub/Sub）、Gateway 调用（Langfuse 单例）、Rate Limiter、MinIO 存储封装（storage/）、异步任务工具（async_utils） |
| `context` | 无 | Prompt 构建（PromptBuilder + builder_helpers）、上下文裁剪（Trimmer）、Token 预算管理、监控 API（ContextMonitor）、23 个 Jinja2 模板 |
| `graph` | 无 | LangGraph Agent 创建/执行（AgentService）、6 个 SubAgent（搜索/记忆/代码/HA/多模态/文档）、多模态直连推理、推理取消（InferenceService）、GPU 锁互斥 |
| `media` | MediaAttachment, DocumentChunkEmbedding | 媒体上传/下载、文档解析（Gateway）+ RAG 向量分块（1024 维 pgvector）、解析缓存（Redis + MinIO 双层）、过期清理任务、音频工具（PCM->WAV） |
| `memory` | UserMemory, UserMemoryEmbedding | 用户记忆 CRUD、pgvector 混合搜索（0.7 向量 + 0.3 关键词）、Embedding 生成、每日/每月总结、GPU 互斥（task_helpers） |
| `models` | ModelConfig | LLM 模型配置（tool/multimodal/embedding）CRUD、SM4 加密密钥、活跃模型查询、API Key 三态处理 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权（httpOnly Cookie）、SSO 冲突、SM3/SM4 加密、家庭成员管理（member_service）、访客过期清理 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | WebSocket 语音流 -> ASR 流式转录 -> Agent Pipeline（纯口语 Prompt）-> TTS 流式合成、声纹注册/识别、设备管理、ambient 环境监听（014，VAD 不触发 active_conv + ASR 自动重连）、响应决策 |
| `agent` | 无 | Prompt 模板目录（fallback_router.j2 拒识兜底与功能推荐） |

---

## 模块依赖关系

```
users <- (所有模块依赖 SysUser)
common <- (所有模块依赖中间件/异常/响应格式/storage/async_utils)
models <- chat, graph, memory, voice（LLM 模型配置）
context <- graph（PromptBuilder 构建上下文）
memory <- graph（Agent 记忆搜索工具）
media <- chat（消息附件关联）、voice（音频持久化）、graph（文档解析工具）
chat <- graph（Message 读写）、voice（消息复用）
graph <- voice（AgentService 推理 + InferenceService 任务管理）
voice（独立 WebSocket 入口，依赖 graph/chat/media/users）
```

---

## 各模块文件概览

### chat（8 个源文件 + services/ 子包）

| 文件 | 职责 |
|------|------|
| `models.py` | Message（status 0-3）+ LangGraphExecution；从 media 导入 MediaAttachment（兼容层） |
| `views.py` | 6 个端点：chat(SSE)/messages（排除限流）/generating/stop/resume/reconnect；使用 `request.target_user_id`（015 多用户） |
| `urls.py` | 6 条路由（chat 核心路由） |
| `serializers.py` | ChatRequest/HistoryQuery/MessageResponse/RequestId 序列化器 |
| `repositories.py` | MessageRepository + ExecutionRepository |
| `sse.py` | 兼容层 -> `apps.common.sse` |
| `tasks.py` | 兼容层 -> `apps.media.tasks` |
| `services/` | ChatService/HistoryService/generation/types；其余为兼容层转发 |

### common（14 个源文件 + storage/ 子包）

| 文件 | 职责 |
|------|------|
| `middleware.py` | TokenAuthMiddleware（Cookie SM4 认证）、`_resolve_target_user()` 多用户解析（015）、set/clear_token_cookie |
| `websocket_auth.py` | WebSocketTokenAuthMiddleware（ASGI WS 认证，无 Cookie 放行给 Consumer 处理设备认证） |
| `exceptions.py` | 异常层级（Auth/LLM/Business/ExternalService）、`map_llm_exception()` |
| `responses.py` | `api_response()`/`error_response()` + `ApiResponse` 类 |
| `event_service.py` | EventService（Redis Pub/Sub），EventType: logout/message/heartbeat/context_status/inference_cancel/doc_parse_progress |
| `gateway_utils.py` | Gateway HTTP 工具集、Langfuse span 记录（`start_observation()`）、tenacity 重试 |
| `tokenizer.py` | tiktoken Token 计数（cl100k_base 编码，单例） |
| `sse.py` | SSE 视图辅助：`parse_sse_request()`、`make_sse_response()` |
| `rate_limiter.py` | Redis INCR 通用速率限制 |
| `async_utils.py` | `cancel_task()`/`cancel_task_sync()` 异步任务取消工具 |
| `decorators.py` | `async_csrf_exempt` 装饰器 |
| `views.py` | EventsView（ASGI 异步 SSE 事件流，Redis Pub/Sub + 30s 心跳） |
| `urls.py` | `GET /api/v1/events` |
| `storage/minio_service.py` | MinIO 封装（upload/download/delete/presigned_url，懒初始化单例） |

### context（7 个源文件 + 23 个 Jinja2 模板）

| 文件 | 职责 |
|------|------|
| `types.py` | MessageRole/PromptMessage/PromptConfig/RetrievedMemory/ToolDefinition/TokenBreakdown/PromptModule |
| `builder.py` | PromptBuilder 组装引擎 + 模块注册 + 兼容常量 |
| `builder_helpers.py` | `format_memory_block()`/`format_tool_context()`/`pair_conversation_turns()` |
| `trimmer.py` | 消息裁剪器（4 级优先级：PROTECTED/FIRST/SECOND/LAST） |
| `monitoring.py` | ContextMonitor（AlertLevel: NORMAL/WARNING/CRITICAL） |
| `loader.py` | Jinja2 模板加载器（`render()` 函数） |
| `tokenizer.py` | 兼容层 -> `apps.common.tokenizer` |

模板分类：核心 4 个（system_base/behavior/reasoning/tool_usage）、可选 3 个（code_assist/creative_writing/data_analysis）、上下文块 5 个（memory_context/memory_empty/compaction_context/compaction_task/tool_context/conversation_history）、SubAgent 7 个（search/memory/code/ha/multimodal/document/voice_intent_classify）、记忆任务 3 个（daily_summary/monthly_summary/cronmem_extract）。

### graph（7 个源文件 + services/ + subagents/ + tools/）

| 文件 | 职责 |
|------|------|
| `agent.py` | Agent 工厂（4 个入口：chat/context/memory/cronmem）、`get_llm()`、checkpointer、thread_id |
| `multimodal.py` | `build_multimodal_messages()` + `stream_multimodal_httpx()`（httpx 直连绕过 LangChain） |
| `graph.py` | 独立 Graph 定义（`langgraph dev` 调试入口） |
| `prompts.py` | 兼容层 -> `apps.context` |
| `urls.py` | `POST /api/v1/chat/inference/cancel/` |
| `views.py` | `cancel_inference` 视图 |
| `services/` | AgentService（执行编排）、helpers/（errors/finalize/monitor/prompt）、context_service、inference_service、cancel_monitor、gpu_lock |
| `subagents/` | 6 个 SubAgent（search/memory/code/ha/multimodal/document）+ base + document_parse_helpers |
| `tools/` | 工具集：web_search/memory(5)/python_exec/ha(3)/history/context + user_id 公共工具 |

### media（6 个源文件 + services/ 子包）

| 文件 | 职责 |
|------|------|
| `models.py` | MediaAttachment（含解析缓存字段、embedding_status）+ DocumentChunkEmbedding（1024 维 pgvector） |
| `views.py` | upload_media/get_media/parse_document/get_parse_task_status/result；使用 `request.target_user_id`（015） |
| `repositories.py` | MediaAttachmentRepository（CRUD + 过期查询 + 消息关联） |
| `serializers.py` | MediaAttachmentSerializer + DocumentParseRequestSerializer |
| `urls.py` / `document_urls.py` | 媒体路由 + 文档解析路由 |
| `tasks.py` | Celery 定时任务：`clean_expired_media`（连续 10 次失败中止） |
| `services/upload.py` | 文件校验 + MinIO 存储 + 元数据持久化（补偿删除） |
| `services/document.py` | Gateway 文档解析、轮询、SSE 进度通知（默认模型 qwen3.5-9b） |
| `services/document_cache.py` | Redis + MinIO 双层解析缓存 |
| `services/document_rag.py` | 向量分块 + RAG 搜索（pgvector 1024 维） |
| `services/image.py` | Pillow 图片宽高 |
| `services/video.py` | ffprobe 时长 + ffmpeg 预处理 |
| `services/audio.py` | PCM 合并 WAV、时长计算 |

### memory（8 个源文件）

| 文件 | 职责 |
|------|------|
| `models.py` | UserMemory（4 种 type: memory/compaction/daily-summary/monthly-summary）+ UserMemoryEmbedding（1024 维） |
| `repositories.py` | MemoryRepository + EmbeddingRepository（ORM + pgvector + pg_jieba 全文检索） |
| `services.py` | EmbeddingClient（向量生成）+ MemoryService（CRUD/搜索/总结） |
| `task_helpers.py` | GPU 互斥：`has_active_users()`/`warmup_language_model()`/`collect_content()`/`run_summary()` |
| `tasks.py` | Celery 任务：embedding 生成、重试、每日/每月总结、健康检查 |
| `views.py` | REST API：列表/创建、详情/更新/删除、搜索 |
| `serializers.py` | 6 个序列化器 |
| `urls.py` | `/memories/`、`/memories/<id>/`、`/memories/search/` |

### models（8 个源文件）

| 文件 | 职责 |
|------|------|
| `models.py` | ModelConfig（tool/multimodal/embedding 3 种类型、SM4 加密 api_key、`effective_context_window` 属性） |
| `services.py` | SM4 加解密、API Key 脱敏、`get_active_model()` 内部明文接口 |
| `repositories.py` | get_all / get_by_id / get_active_by_type / update |
| `views.py` | ModelListView / ModelDetailView（仅管理员） |
| `serializers.py` | ModelResponseSerializer + ModelUpdateSerializer |
| `permissions.py` | `IsAdminUser`（检查 request.user_type） |
| `urls.py` | `/models/`、`/models/<id>/` |
| `admin.py` | Django Admin 配置 |

### users（12 个源文件）

| 文件 | 职责 |
|------|------|
| `models.py` | SysUser（type: admin/user，member_type: member/guest，guest_expires_at，锁定/统计字段） |
| `services.py` | CaptchaService（生成/校验）+ AuthService（登录/登出、SSO 冲突） |
| `member_service.py` | MemberService（家庭成员列表/创建，含 Gateway 声纹注册 + ffmpeg 音频转换）（015 新增） |
| `repositories.py` | 数据访问层（ORM + @sync_to_async），含 `list_members()` |
| `views.py` | CaptchaView / LoginView / LogoutView / MeView / MemberListCreateView |
| `serializers.py` | LoginRequestSerializer / CreateMemberSerializer / MemberListSerializer |
| `crypto.py` | SM3 哈希、SM4 加解密、Token 生成、`generate_token_hash()` |
| `exceptions.py` | UsernameExistsError / VoiceprintRegistrationError |
| `tasks.py` | Celery 定时任务：`expire_guests`（过期访客 status=0） |
| `urls.py` / `member_urls.py` | 认证路由 + 成员管理路由 |
| `management/commands/` | `init_admin`（初始化管理员）、`reset_all_data`（全量清库重建） |

### voice（12 个源文件 + services/ 子包 14 个文件）

| 文件 | 职责 |
|------|------|
| `consumers.py` | VoiceConsumer 骨架（3 Mixin 组装 + connect/disconnect/receive + 设备 Token 认证 + TTS Channels 分组） |
| `consumer_events.py` | EventMixin — ASR 事件分发 + ambient 分支（VAD 跳过 active_conv + 停止词预检 + 聚合器路由 + ASR 错误重连） |
| `consumer_inference.py` | InferenceMixin — VoicePipeline 后台启动、空闲超时（ambient 跳过） |
| `consumer_session.py` | SessionMixin — ASR 连接/配置/断开、语音段超时、ambient 聚合初始化 + ASR 自动重连 |
| `models.py` | SpeakerProfile / RegisteredDevice / VoiceSettings |
| `repositories.py` | 3 个 Repo |
| `serializers.py` | 6 个序列化器 |
| `views.py` | REST 视图：声纹/设备/语音设置 CRUD |
| `urls.py` / `routing.py` | REST 路由 + WebSocket 路由（`ws/voice/`） |
| `services/ws_client_base.py` | BaseWSClient（ASR/TTS 共用连接/心跳/接收循环基类） |
| `services/asr_stream_client.py` | ASRStreamClient（Gateway ASR 流式转录） |
| `services/tts_stream_client.py` | TTSStreamClient（Gateway TTS 流式合成） |
| `services/tts_pipeline_manager.py` | TTS 管道管理（安慰语音、队列、cancel） |
| `services/tts_router.py` | TTSRouter（group_send 跨设备 TTS 广播） |
| `services/voice_pipeline.py` | VoicePipeline 编排（ASR -> 纯口语 Prompt -> Agent -> TTS，barge-in 超时跳过） |
| `services/voice_persist_service.py` | 音频持久化 + `record_only_ambient()` |
| `services/voice_session_service.py` | Redis 会话管理 + 频率限制 |
| `services/voice_settings_service.py` | 语音设置 CRUD |
| `services/voice_messages.py` | 协议消息工具（error_msg/response_event/delta_msg） |
| `services/response_decision_service.py` | ambient 响应决策（8 级决策链 + LLM 意图分类） |
| `services/utterance_aggregator.py` | 话语聚合器（3s 超时 + 最大 10 段缓冲） |
| `services/speaker_service.py` | 声纹注册/删除（Gateway 对接） |
| `services/device_service.py` | 设备注册/管理（SM4 Token 加密） |

### agent（仅模板目录）

| 文件 | 职责 |
|------|------|
| `prompts/fallback_router.j2` | 拒识兜底与功能推荐 Prompt 模板 |


<claude-mem-context>

</claude-mem-context>