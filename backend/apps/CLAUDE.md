# Apps 模块总览

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## App 列表

| App | 关键模型 | 说明 |
|-----|----------|------|
| `chat` | Message, LangGraphExecution | 消息收发、SSE 流式响应、推理取消 |
| `common` | 无 | Token 中间件、异常体系、响应格式、SSE 事件、Gateway 调用（Langfuse 单例）、Rate Limiter、MinIO 存储封装（storage/）、异步任务工具（async_utils） |
| `context` | 无 | Prompt 构建（PromptBuilder + builder_helpers）、上下文裁剪（Trimmer）、Token 预算管理、监控 API（ContextMonitor）、16 个 Jinja2 模板 |
| `graph` | 无 | LangGraph Agent 创建/执行（AgentService）、6 个 SubAgent（搜索/记忆/代码/HA/多模态/文档）、推理取消（InferenceService）、GPU 锁互斥 |
| `media` | MediaAttachment, DocumentChunkEmbedding | **010 新增 — 从 chat 分离**。媒体上传/下载、文档解析（Gateway）+ RAG 向量分块（011 新增）、过期清理任务、音频工具（PCM→WAV） |
| `memory` | UserMemory, UserMemoryEmbedding | 用户记忆 CRUD、pgvector 向量搜索、Embedding 生成、每日/每月总结、task_helpers GPU 互斥 |
| `models` | ModelConfig | LLM 模型配置（tool/multimodal/embedding）CRUD、SM4 加密密钥、活跃模型查询 |
| `users` | SysUser | 验证码、登录/登出、Token 鉴权（httpOnly Cookie）、SSO 冲突、SM3/SM4 加密 |
| `voice` | SpeakerProfile, RegisteredDevice, VoiceSettings | **010 重构**。WebSocket 语音流 → ASR 流式转录 → Agent Pipeline → TTS 流式合成、声纹注册/识别、设备管理、响应决策 |

---

## 模块依赖关系

```
users ← (所有模块依赖 SysUser)
common ← (所有模块依赖中间件/异常/响应格式)
models ← chat, graph, memory, voice（LLM 模型配置）
context ← graph（PromptBuilder 构建上下文）
memory ← graph（Agent 记忆搜索工具）
media ← chat（消息附件关联）、voice（音频持久化）、graph（文档解析工具）
chat ← graph（Message 读写）、voice（消息复用）
graph ← voice（AgentService 推理 + InferenceService 任务管理）
voice（独立 WebSocket 入口，依赖 graph/chat/media/users）
```

---

## 重要重构记录

### voice 模块代码精简重构（2026-03-11）

1. **新增公共工具**: `apps/common/async_utils.py`（异步任务取消工具，消除 8+ 处重复代码）
2. **新增 WebSocket 基类**: `voice/services/ws_client_base.py`（BaseWSClient，ASR/TTS 客户端共用连接/心跳/接收循环）
3. **新增协议消息工具**: `voice/services/voice_messages.py`（error_msg/response_event/delta_msg/build_agent_error）
4. **voice_persist_service 扩展**: 新增 `persist_audio_attachment()`（从 VoicePipeline 迁入）和 `record_only_ambient()`（从 VoicePipeline 迁入）
5. **voice_session_service 扩展**: 新增 `check_ws_rate_limit()`（从 consumers.py 提取）和 `check_llm_rate_limit()`（从 voice_pipeline.py 提取）
6. **代码量**: 14 个文件变更，2404 行 → ~1500 行（减少 38%），所有服务文件 ≤ 150 行

### 010-voice-agent-pipeline 关键变更

1. **media app 新增**: 从 chat 分离独立的媒体管理模块（models/repositories/services/views/tasks）
2. **voice Consumer 重构**: 单文件 → 3 Mixin 架构（SessionMixin/EventMixin/InferenceMixin）
3. **voice services 重构**: gateway_client.py/voice_context_service.py 删除，新增 asr_stream_client.py/tts_stream_client.py/voice_pipeline.py/voice_persist_service.py
4. **context builder_helpers**: 辅助函数从 builder.py 提取到 builder_helpers.py
5. **graph 服务拆分**: agent_service 辅助函数提取到 agent_helpers.py，新增 context_service.py/inference_service.py/gpu_lock.py/cancel_monitor.py
