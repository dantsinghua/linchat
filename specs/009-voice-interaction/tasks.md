# Tasks: 语音交互

**Input**: Design documents from `/specs/009-voice-interaction/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Phase 9 包含完整测试任务，覆盖宪法 3.1 要求（服务层 95%、总体 80%+）。

**Organization**: 任务按用户故事分组，支持独立实现和测试每个故事。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 任务所属用户故事（US1, US2, US4, US5, US6）
- 所有路径均为相对于项目根目录的绝对路径

---

## Phase 1: Setup（项目基础设施）

**Purpose**: 安装依赖、创建应用骨架、配置基础运行环境

- [X] T001 安装后端新依赖 `channels>=4.0` `channels-redis>=4.0` `websockets>=12.0` `pypinyin>=0.51`（唤醒词拼音模糊匹配，T041 依赖），更新 `backend/requirements.txt`
- [X] T002 创建 `backend/apps/voice/` Django 应用骨架（`__init__.py`, `apps.py`, `admin.py`, `services/__init__.py`）
- [X] T003 [P] 配置 `backend/core/settings.py`：添加 `channels` 和 `apps.voice` 到 INSTALLED_APPS，添加 CHANNEL_LAYERS（Redis DB3，独立于 DB0 缓存/DB1 Langfuse/DB2 Celery Broker）和语音相关配置常量（VOICE_SESSION_TTL, VOICE_ACTIVE_CONV_TTL, VOICE_AUDIO_CACHE_TTL, VOICE_MAX_RECORDING_SECONDS, VOICE_DEFAULT_WAKE_WORDS, VOICE_SPEAKER_THRESHOLD, VOICE_VAD_THRESHOLD）。⚠️ 术语映射：VoiceSettings.vad_sensitivity 与 llmgateway session.configure 的 vad_threshold 为同一值直接传递（值域 0.0~1.0，越大越不灵敏），服务层转发时字段名转换：vad_sensitivity → vad_threshold
- [X] T004 [P] 添加环境变量到 `backend/.env`：`LLM_GATEWAY_WS_URL=ws://127.0.0.1:8888`、`LLM_GATEWAY_WS_API_KEY`、`LLM_GATEWAY_HTTP_URL=http://127.0.0.1:8889`（声纹注册/管理 HTTP REST 端点 base URL。⚠️ 不可使用 8081，已被 Langfuse Nginx 占用）；前端 `frontend/.env.local` 添加 `NEXT_PUBLIC_WS_BASE_URL=/linchat/ws/voice/`（WebSocket 端点路径，避免从 API base URL 字符串替换推导）
- [X] T005 改造 `backend/core/asgi.py`：引入 `ProtocolTypeRouter`，HTTP 请求走 `get_asgi_application()`，WebSocket 请求通过 `WebSocketTokenAuthMiddleware`（T005a）路由到 voice routing。⚠️ 不使用 Django Channels 的 AuthMiddlewareStack（不兼容 LinChat 的 SM4 Token-in-Cookie 认证机制）
- [X] T005a [P] 创建自定义 WebSocket 认证中间件 `backend/apps/common/websocket_auth.py`：实现 `WebSocketTokenAuthMiddleware`（ASGI middleware 协议），复用现有认证逻辑：① 从 `scope['cookies']` 读取 `linchat_token`（Cookie 认证，Web 端）；② SM4 解密验证（`apps/users/crypto.sm4_decrypt`）；③ SHA256 Hash 计算（`apps/users/crypto.generate_token_hash`）；④ 异步 Redis 查询 Token 数据（`core/redis.redis_get` + `get_token_key`）；⑤ 24h 绝对过期检查 + TTL 刷新（`core/redis.redis_expire`）；⑥ 认证成功设置 `scope['user_id']`/`scope['username']`/`scope['user_type']`；⑦ 认证失败发送 WebSocket close(4001) 并关闭连接。⚠️ 不处理设备 API Token 认证（由 T037 在 consumer connect 中处理 query 参数 token）。此中间件仅替代 Django Channels 的 AuthMiddlewareStack，复用 `middleware.py` 的 `_verify_token_sync` 逻辑改写为异步版本
- [X] T006 [P] 配置 Nginx WebSocket 路由：在 `/etc/nginx/sites-available/deeptutor` 添加 `location /linchat/ws/` 块，proxy_pass 到 linchat_backend，设置 Upgrade/Connection 头和 86400s 超时
- [X] T006a [P] 创建模块文档：`backend/apps/voice/CLAUDE.md`（Voice 应用概述、服务层说明、WebSocket 消费者说明）、`frontend/src/components/voice/CLAUDE.md`（语音组件说明），满足宪法第七条模块文档要求

**Checkpoint**: 基础设施就绪 — Django Channels 可启动，WebSocket 路由可达（未认证时返回 close(4001)）

---

## Phase 2: Foundational（阻塞性前置）

**Purpose**: 数据模型、迁移、基础路由和前端类型 — 所有用户故事的公共依赖

**⚠️ CRITICAL**: 此阶段完成前不能开始任何用户故事

- [X] T007 [P] 扩展 Message 模型：在 `backend/apps/chat/models.py` 新增 `is_voice`（BooleanField, default=False, db_index=True）和 `speaker_id`（CharField(100), null=True, blank=True）
- [X] T008 [P] 创建 Voice 应用模型：在 `backend/apps/voice/models.py` 定义 SpeakerProfile（user OneToOne, gateway_speaker_id unique CharField(100), name CharField(50), quality_score FloatField null=True, enrolled_at）、RegisteredDevice（device_uuid unique CharField(36), user FK, name, api_token_encrypted, token_prefix CharField(8) indexed, is_active, last_active_at）、VoiceSettings（user OneToOne, wake_words JSONField default=["小鱼"], recording_mode CharField choices=['hold','toggle'], vad_sensitivity FloatField default=0.5）
- [X] T009 生成并执行数据库迁移：`backend/apps/chat/migrations/0005_message_voice_fields.py`（Message 新增字段，0004 已被 remove_thumbnail_add_document_type 占用）+ `backend/apps/voice/migrations/0001_initial.py`（三个新模型）+ `backend/apps/voice/migrations/0002_create_unknown_user.py`（预创建 username="unknown" 全局单例用户，status=0（SysUser.is_active() 返回 False，不可登录））
- [X] T010 [P] 创建数据访问层 `backend/apps/voice/repositories.py`：SpeakerProfileRepository（按 gateway_speaker_id 查 user、按 user_id 查/删）、RegisteredDeviceRepository（按 token_prefix 查设备、按 user_id 查列表）、VoiceSettingsRepository（get_or_create 用户设置）
- [X] T011 [P] 扩展聊天序列化器 `backend/apps/chat/serializers.py`：在 MessageSerializer 中添加 is_voice、speaker_id 字段，音频附件信息通过 attachments 序列化返回
- [X] T012 [P] 创建 REST URL 路由 `backend/apps/voice/urls.py`：注册 `/api/v1/voice/speakers/`、`/api/v1/voice/devices/`、`/api/v1/voice/settings/` 路径（视图占位），并在 `backend/core/urls.py` include voice urls
- [X] T013 [P] 创建 WebSocket 路由 `backend/apps/voice/routing.py`：定义 `ws/voice/` 路径指向 VoiceConsumer（占位 consumer）
- [X] T014 [P] 创建前端类型定义 `frontend/src/types/voice.ts`：VoiceMessage、SpeakerProfile、RegisteredDevice、VoiceSettings、WebSocket 消息类型（VoiceWSMessage, VoiceWSEvent 等）、VoiceSessionState 枚举
- [X] T015 [P] 创建前端语音全局状态 `frontend/src/stores/voiceStore.ts`（Zustand）：voiceMode（开关状态）、sessionState（idle/configuring/listening/processing/responding/interrupted）、isRecording、currentTranscription、error 等状态 + 对应 actions
- [X] T016 [P] 创建前端语音 REST API 服务 `frontend/src/services/voiceApi.ts`：封装声纹 CRUD、设备 CRUD、语音设置 GET/PUT 的 HTTP 请求函数

**Checkpoint**: 基础就绪 — 模型已迁移、路由已注册、前端类型和状态管理已定义。用户故事实现可以开始

---

## Phase 3: User Story 1 — 语音模式对话 (Priority: P1) 🎯 MVP

**Goal**: 用户在聊天界面开启语音模式后，通过 WebSocket 实时流式传输录音 → 服务端代理到 llmgateway → AI 文字回复流式显示。无需手动点击发送

**Independent Test**: 开启语音模式 → 录一段话 → 停止录音 → 验证 AI 流式文字回复自动显示，同时语音消息（含 STT 转写 + 音频附件）已持久化到数据库

### Implementation for User Story 1

**⚠️ 日志要求（宪法 6.2 合规）**: 所有服务层代码 MUST 添加结构化日志 — INFO 级别记录业务关键操作（WebSocket 连接建立/断开、语音会话创建/关闭、消息持久化、声纹注册/识别）；WARNING 记录可恢复异常（STT 转写超时、llmgateway 可恢复错误）；ERROR 记录不可恢复异常。外部服务调用（llmgateway HTTP/WS）MUST 记录请求/响应概要和耗时。此要求适用于 Phase 3-8 所有服务层任务，不单独列为独立任务

- [X] T017 [P] [US1] 实现 llmgateway WebSocket 客户端 `backend/apps/voice/services/gateway_client.py`：异步 WebSocket 连接管理（connect/disconnect/reconnect）、Binary 帧转发（PCM16 音频）、JSON 事件接收与分发、session.configure 发送、心跳保活、错误处理（recoverable/非 recoverable）
- [X] T018 [P] [US1] 实现语音会话服务 `backend/apps/voice/services/voice_session_service.py`：Redis 会话状态管理（voice:session:{user_id} TTL=120s，每次收到客户端活动时刷新 TTL 防止长对话超时）、单会话强制（FR-034）、音频帧缓存（voice:audio_chunks:{user_id}:{segment_id}）、消息持久化（使用 transaction.atomic() 原子写入：创建 user Message is_voice=True content='' + MediaAttachment audio + assistant Message，任一步骤失败全部回滚，宪法 1.3 合规）、音频文件保存到 MinIO（PCM16 帧合并 + WAV 头 → 存储，计算音频时长 = 总帧字节数 / 2 / 16000 秒 → 写入 MediaAttachment.duration_seconds）、**异步 STT 转写子流程**：`vad.speech_end` 后将缓存的音频帧合并为 WAV 文件（PCM16 + 44-byte WAV 头）→ 设置 Redis `voice:stt_pending:{user_id}:{segment_id}` 状态为 pending → 异步调用 HTTP `POST /v1/chat/completions`（model=minicpm-o, prompt="请逐字转写以下音频内容，只输出转写文字", 音频通过 data:audio/wav;base64 传递）→ 转写完成后更新 Message.content → 通过 WebSocket 发送 LinChat 自行生成的 `transcription.complete` 事件到客户端（含 text + message_id）。**STT 失败处理**：HTTP 调用超时（30s）或返回错误时，设置 Redis `voice:stt_pending` 状态为 failed → 发送 `transcription.failed` 事件到客户端（含 error + message_id）→ 前端显示"语音转写失败"标签，Message.content 保持为空字符串（音频附件仍可播放）。**STT 与消息持久化时序协调**：STT 转写结果先缓存到 Redis `voice:stt_result:{user_id}:{segment_id}`（TTL=120s）。消息持久化时（response.end 触发）检查该 key：若已有转写结果则直接写入 Message.content；若 STT 尚未完成，Message.content 暂存空字符串，STT 完成后再异步更新。voice_chat 模式（auto_respond=true）下两个流程并行，任一先完成均能正确处理
- [X] T019 [US1] 实现 WebSocket 消费者 `backend/apps/voice/consumers.py`：继承 AsyncWebSocketConsumer，Cookie 认证（Web 端）、session.configure 处理（创建上游 llmgateway 连接 voice_chat 模式）、Binary 帧透传到 llmgateway、llmgateway 事件处理（vad.* 转发、response.start 转发并记录 response_id、response.delta 转发注意 `data.delta.content` 嵌套结构、response.end 转发并使用 `response_id` 匹配 + usage 含 `input_tokens`/`output_tokens`/`audio_duration_ms` + 触发消息持久化并发送 message.saved）、**response.cancel 后不期望 response.end 需主动清理状态**、跟踪 response_id 用于 cancel 匹配、session.close 处理（断开上游连接、清理 Redis 状态）、**连接空闲超时检测（60 秒未收到客户端 Ping/消息时主动断开并清理 Redis 会话状态，可配合 uvicorn `--ws-ping-interval 30 --ws-ping-timeout 60` 参数在服务器层面处理）**、连接断开清理。注：llmgateway 仅维护最近 5 轮对话历史，超出后早期上下文自动移除，LinChat 不做补偿（已在 spec.md 文档化为已知限制）。注：llmgateway 无 transcription.* 事件，STT 转写由 voice_session_service 异步 HTTP 完成
- [X] T020 [P] [US1] 创建 AudioWorklet PCM16 采集 Hook `frontend/src/hooks/usePCMAudioCapture.ts`：AudioContext (sampleRate: 16000) + AudioWorklet Processor、每 30ms 输出一帧 Int16Array (960 bytes)、麦克风权限请求、开始/停止录音控制、30 秒最大录音时长自动停止（FR-007）、音量级别回调（供波形显示）
- [X] T021 [P] [US1] 实现语音 WebSocket 连接管理 Hook `frontend/src/hooks/useVoiceWebSocket.ts`：WebSocket 连接建立/断开（通过 Nginx 时使用 `ws://{host}/linchat/ws/voice/` 或 `wss://`，使用 `NEXT_PUBLIC_WS_BASE_URL` 环境变量（T004 配置），回退到从 `NEXT_PUBLIC_API_BASE_URL` 推导）、session.configure 发送、Binary 帧发送（PCM16 音频）、JSON 事件接收与分类分发（vad/transcription/response/error/message.saved）、**心跳保活（每 30 秒发送 WebSocket Ping 帧，使用 setInterval 周期发送，连接断开时清除定时器）**、自动重连逻辑、连接状态管理
- [X] T022 [US1] 实现语音模式状态机 Hook `frontend/src/hooks/useVoiceMode.ts`：整合 usePCMAudioCapture + useVoiceWebSocket + voiceStore，状态流转（idle → configuring → listening → processing → responding → idle）、录音模式切换（hold/toggle，FR-006）、收到 LinChat 自行生成的 `transcription.complete` 事件时更新用户消息 content 显示、response.delta 时解析 `data.delta.content`（嵌套结构）流式追加 AI 回复、response.end + message.saved 时完成消息、错误处理与降级提示
- [X] T023 [P] [US1] 创建实时音频波形组件 `frontend/src/components/voice/VoiceWaveform.tsx`：接收音量级别数据、Canvas 或 SVG 绘制实时波形动画、录音状态指示器（FR-009）
- [X] T024 [US1] 创建语音模式控制面板 `frontend/src/components/voice/VoiceModePanel.tsx`：底部弹出式面板、录音按钮（支持按住/点击两种模式）、VoiceWaveform 嵌入、录音时长显示、当前状态文字（录音中/处理中/AI 回复中）、错误提示区域、关闭语音模式按钮、声纹注册提示（FR-018a：首次进入语音模式时通过 voiceApi 检查当前用户 SpeakerProfile 是否存在，若未注册则显示一次性非阻塞提示"建议注册声纹以支持共享设备使用"，用户可点击关闭或跳转设置页）
- [X] T025 [US1] 扩展聊天输入区 `frontend/src/components/chat/MessageInput.tsx`：添加语音模式切换按钮（麦克风图标）、语音模式开启时隐藏文字输入区显示 VoiceModePanel、语音模式关闭时恢复文字输入
- [X] T026 [US1] 集成语音模式到聊天页面布局：确保 VoiceModePanel 作为底部弹出层正确覆盖在聊天界面上、聊天记录在语音模式下仍可见可滚动

**Checkpoint**: MVP 功能完整 — 用户可以通过语音模式录音 → WebSocket 传输 → AI 文字流式回复 → 消息持久化到数据库

---

## Phase 4: User Story 2 — 流式回复打断 (Priority: P2)

**Goal**: AI 流式生成文字回复时，用户可通过点击停止按钮或开始新录音打断 AI 输出，系统立即停止流式文字并保留对话上下文

**Independent Test**: AI 正在流式输出文字时，点击停止按钮验证文字立即停止；开始新录音验证文字停止且新录音开始

**Depends on**: Phase 3 (US1)

### Implementation for User Story 2

- [X] T027 [US2] 添加打断处理到 WebSocket 消费者 `backend/apps/voice/consumers.py`：接收客户端 response.cancel 消息后转发到 llmgateway（**必须携带 response_id**）、cancel 后 llmgateway **不发送 response.end**，LinChat 需主动标记回复为 interrupted 并清理状态（停止累积 response.delta、保留已累积的部分回复）、保留对话上下文供下轮使用（FR-012）。**打断后主动触发消息持久化**：使用 transaction.atomic() 原子写入 user Message（is_voice=True, content='' 待 STT 补充）+ MediaAttachment（音频附件）+ assistant Message（content=已累积的部分回复文本），assistant 消息通过现有 SSE interrupted 消息类型标记为截断状态（宪法 1.2 消息类型规范）
- [X] T028 [US2] 实现前端打断逻辑 `frontend/src/hooks/useVoiceMode.ts`：responding 状态下点击停止 → 发送 response.cancel → 状态切换到 idle、responding 状态下开始新录音 → 发送 response.cancel → 状态切换到 listening、被打断的 AI 回复标记为截断（在消息列表中显示不完整标记）
- [X] T029 [US2] 更新 VoiceModePanel 打断交互 `frontend/src/components/voice/VoiceModePanel.tsx`：AI 回复中显示停止按钮（替代录音按钮）、AI 回复中点击录音按钮触发打断+新录音
- [X] T030 [US2] 验证对话上下文保留：打断后用户发送新语音，AI 应基于打断前的完整上下文（含被截断的回复）继续对话

**Checkpoint**: 打断功能完整 — 用户可以随时停止 AI 输出并继续新对话

---

## Phase 5: User Story 4 — 声纹注册与识别 (Priority: P4)

**Goal**: 用户在设置页面注册声纹，系统通过 llmgateway 声纹服务建立 speaker_id ↔ user_id 映射。共享设备（外部客户端）连接时通过声纹自动识别说话人身份

**Independent Test**: 两位用户分别注册声纹后，通过 WebSocket 发送各自语音，验证消息正确归属到对应用户

**Depends on**: Phase 3 (US1) — 需要 WebSocket 基础设施

### Implementation for User Story 4

- [X] T031 [P] [US4] 实现声纹服务 `backend/apps/voice/services/speaker_service.py`：register_speaker（接收音频文件 → 调用 llmgateway `POST /v1/voice/speakers` → 获取 speaker_id 和 quality_score → 创建 SpeakerProfile 映射并保存 quality_score）、delete_speaker（删除本地映射 + 调用 llmgateway `DELETE /v1/voice/speakers/{speaker_id}`）、identify_speaker（gateway_speaker_id → 查 SpeakerProfile → 返回 LinChat user_id）、list_speakers（返回所有已注册声纹）
- [X] T032 [P] [US4] 实现设备管理服务 `backend/apps/voice/services/device_service.py`：register_device（生成 UUID + 随机 Token → SM4 加密 → 存储 RegisteredDevice → 返回明文 Token，仅一次）、revoke_device（设为 is_active=False）、authenticate_by_token（取前 8 位查 token_prefix → SM4 解密全量比对 → 更新 last_active_at）、list_devices
- [X] T033 [P] [US4] 创建语音应用序列化器 `backend/apps/voice/serializers.py`：SpeakerProfileSerializer、RegisteredDeviceSerializer（隐藏 api_token_encrypted）、VoiceSettingsSerializer、VoiceSettingsUpdateSerializer、CreateDeviceSerializer（输入 name）、CreateSpeakerSerializer（输入 audio + name）
- [X] T034 [US4] 实现声纹管理 REST 视图 `backend/apps/voice/views.py`：SpeakerListCreateView（GET 列表 + POST 注册，multipart/form-data 接收音频）、SpeakerDeleteView（DELETE 删除指定声纹）。所有响应 MUST 遵循宪法 1.2 统一格式 `{"code": "SUCCESS/ERROR_CODE", "data": {...}, "message": "..."}`
- [X] T035 [US4] 实现设备管理 REST 视图 `backend/apps/voice/views.py`：DeviceListCreateView（GET 列表 + POST 注册）、DeviceDeleteView（DELETE 撤销设备）。响应格式同 T034
- [X] T036 [US4] 添加声纹识别处理到 WebSocket 消费者 `backend/apps/voice/consumers.py`：处理 llmgateway `speaker.identified` 事件 → 当 `identified=true` 时调用 speaker_service.identify_speaker 查本地匹配表（匹配成功：增加 user_id/user_name 字段后转发给客户端，消息持久化时使用声纹识别出的 user_id 而非连接认证的 user_id；匹配表无记录：发送 SPEAKER_NOT_FOUND error 事件，消息归属 unknown 用户）→ 当 `identified=false` 时（llmgateway 判定置信度低于 speaker_threshold），发送 SPEAKER_NOT_FOUND error 事件，消息归属 unknown 用户（FR-018）→ 将 speaker_id 添加到 Redis `voice:recent_speakers:{owner_user_id}` Set（SADD + EXPIRE 60s，供 T041 响应决策多因素判断使用：≥2 个不同 speaker 活跃时降低自动回复倾向）
- [X] T037 [US4] 添加设备 API Token 认证到 WebSocket 消费者 `backend/apps/voice/consumers.py`：connect 时检查 query 参数 `token` → 调用 device_service.authenticate_by_token → 认证成功获取设备关联 user_id → 认证失败关闭连接
- [X] T038 [P] [US4] 创建声纹管理前端组件 `frontend/src/components/settings/SpeakerProfileCard.tsx`：已注册声纹列表（名称 + 注册时间）、注册新声纹（引导录制 10-30 秒 → 上传 → 显示结果含质量评分）、删除声纹确认对话框、录音复用 `usePCMAudioCapture` Hook 录制 PCM16，录制完成后合并帧并添加 44-byte WAV 头，通过 HTTP multipart/form-data 上传 WAV 文件（llmgateway 仅接受 WAV 格式）
- [X] T039 [P] [US4] 创建设备管理前端组件 `frontend/src/components/settings/DeviceManageCard.tsx`：已注册设备列表（名称 + 状态 + 最后活跃时间）、注册新设备（输入名称 → 显示生成的 API Token，提示仅此一次可见）、撤销设备确认对话框
- [X] T040 [US4] 集成语音管理到设置页面 `frontend/src/app/settings/page.tsx`：添加声纹管理区域（SpeakerProfileCard）和设备管理区域（DeviceManageCard）

**Checkpoint**: 声纹注册与设备管理完整 — 用户可注册/删除声纹，外部设备可通过 Token 认证连接，声纹识别可自动归属消息

---

## Phase 6: User Story 5 — 智能响应决策 (Priority: P5)

**Goal**: 服务端基于唤醒词检测、活跃对话状态和多因素判断，智能决定是回复（RESPOND）、仅记录（RECORD_ONLY）还是停止（STOP）。为外部持续监听客户端接入做预备

**Independent Test**: WebSocket 发送含唤醒词"小鱼"的语音 → 验证系统回复；发送不含唤醒词的普通对话 → 验证系统仅记录不回复

**Depends on**: Phase 5 (US4) — 需要声纹识别能力

### Implementation for User Story 5

- [X] T041 [US5] 实现响应决策服务 `backend/apps/voice/services/response_decision_service.py`：decide(transcription_text, speaker_id, user_id) → RESPOND/RECORD_ONLY/STOP。决策优先级链（短路求值，命中即返回）：① 紧急命令词白名单（"停"/"取消"/"闭嘴"）→ 立即 STOP（FR-020）；② 唤醒词精确匹配（文本包含唤醒词列表中任一词）→ RESPOND（FR-019）；③ 唤醒词模糊匹配（编辑距离 ≤ 1 或拼音相似度 ≥ 0.8）→ RESPOND（FR-019）；④ 活跃对话状态（Redis voice:active_conv:{user_id} 存在）→ RESPOND，无论句式特征（对话延续，FR-021a）；⑤ 非活跃 + 多 speaker 活跃（Redis voice:recent_speakers:{user_id} Set size ≥ 2，TTL=60s）→ RECORD_ONLY（可能是人与人对话，FR-021）；⑥ 非活跃 + 单 speaker + 问句特征（问号/疑问词「吗、呢、吧、什么、怎么、哪、谁、为什么」/语气词）→ RESPOND（FR-021）；⑦ 以上均未命中 → RECORD_ONLY。从 VoiceSettings 加载用户唤醒词列表
- [X] T042 [US5] 添加持续监听模式到 WebSocket 消费者 `backend/apps/voice/consumers.py`：session.configure mode=continuous_listen 时启用 speaker_identify + 关闭 auto_respond → 收到异步 STT 转写结果后调用 response_decision_service.decide（注：持续监听模式下 auto_respond=false，需等 LinChat 异步 STT 转写完成后才能获取文本进行决策）→ RESPOND 时发送 llmgateway `input.commit` 触发推理 → RECORD_ONLY 时仅持久化消息（归属声纹识别用户或 unknown） → STOP 时发送 response.cancel → 发送 decision.result 事件到客户端
- [X] T043 [US5] 实现活跃对话跟踪 `backend/apps/voice/services/voice_session_service.py`：AI 回复完成后设置 voice:active_conv:{user_id} TTL=30s（标记活跃对话）、response_decision_service 检查活跃状态时查询此 key
- [X] T044 [P] [US5] 实现语音设置 REST 视图 `backend/apps/voice/views.py`：VoiceSettingsView（GET 获取设置 get_or_create + PUT 更新设置），包含唤醒词列表、录音模式、VAD 灵敏度。响应格式同 T034。PUT 成功后，若用户当前有活跃 WebSocket 语音会话（检查 Redis voice:session:{user_id}），通过 Channels group_send 向该会话推送设置变更通知，consumer 收到后向 llmgateway 发送 `session.update`（仅更新 vad_threshold、speaker_threshold 等可动态调整的参数，不清空对话历史）
- [X] T045 [P] [US5] 创建语音设置前端组件 `frontend/src/components/settings/VoiceSettingsCard.tsx`：唤醒词管理（添加/删除唤醒词标签列表）、录音模式选择（按住说话 vs 点击切换）、VAD 灵敏度滑块、保存设置按钮。保存成功后，若当前语音模式处于活跃状态，通过 voiceStore 触发设置同步（前端无需额外操作，后端通过 Channels group_send + session.update 自动完成）
- [X] T046 [US5] 集成 VoiceSettingsCard 到设置页面 `frontend/src/app/settings/page.tsx`

**Checkpoint**: 响应决策完整 — 持续监听模式下系统能智能判断是否回复，活跃对话 30 秒超时后需唤醒词重新激活

---

## Phase 7: User Story 6 — 语音对话记忆生成 (Priority: P6)

**Goal**: 所有语音对话（含 RECORD_ONLY 背景对话）持久化存储并参与每日记忆总结。历史消息中语音消息显示 STT 转写文字 + 音频播放器

**Independent Test**: 用户通过语音对话几轮后，验证记忆摘要包含语音内容；查看历史消息，语音消息显示转写文字和播放器

**Depends on**: Phase 3 (US1) — 需要消息持久化基础

### Implementation for User Story 6

- [X] T047 [P] [US6] 创建语音消息展示组件 `frontend/src/components/voice/VoiceMessageBubble.tsx`：显示"[语音消息]"标签 + STT 转写文字、音频播放器（迷你波形 + 播放/暂停按钮 + 时长显示）、播放进度条、音频加载状态处理。波形生成方式：使用 Web Audio API `decodeAudioData` 从音频文件提取采样数据，计算 RMS 振幅生成波形柱状图（~50 个柱），无需后端预计算
- [X] T048 [US6] 扩展消息列表渲染 `frontend/src/components/chat/MessageList.tsx`：检查消息 is_voice 字段 → 语音消息使用 VoiceMessageBubble 组件渲染（替代纯文字气泡）、从 attachments 中提取 media_type='audio' 附件的 URL 和时长
- [X] T049 [US6] 扩展记忆总结以包含语音消息 `backend/apps/memory/tasks.py`：(1) 确认每日记忆任务查询 Message 时包含 is_voice=True 的消息、语音消息的 content（STT 转写文字）正常参与摘要生成；(2) 修改记忆查询逻辑，遍历所有 `is_active=True` 的用户，为每位用户的记忆上下文额外附加 unknown 用户（系统默认用户）当日的 RECORD_ONLY 消息（FR-027 要求所有语音对话包括背景对话均参与记忆总结）；(3) unknown 用户消息在记忆摘要中标注为"背景对话（未识别说话人）"；(4) 避免重复：unknown 消息作为共享上下文附加到每位活跃用户的记忆任务中，而非为 unknown 用户单独生成记忆

**Checkpoint**: 语音记忆完整 — 语音消息在聊天历史中有播放器展示，每日记忆包含语音对话内容

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 降级处理、安全加固、端到端验证

- [X] T050 [P] 实现 llmgateway 降级通知与宪法异常映射：声纹识别失败时回退手动用户选择（FR-039）、MiniCPM-o 推理失败时提示重试或切换文字模式、所有降级状态通过 WebSocket error 事件通知前端（FR-040）。在 `gateway_client.py` 中将 llmgateway WebSocket/HTTP 错误映射到宪法 4.3 异常体系（**全部 7 种异常类型**）：连接失败→LLMConnectionError（重试3次）、超时→LLMTimeoutError（重试3次）、HTTP 429/速率限制→LLMRateLimitError（不重试，返回等待时间）、内容过滤拒绝→LLMContentFilterError（不重试，允许用户修改）、推理异常响应→LLMInvalidResponseError（重试3次）、llmgateway 对话历史超限或 STT 转写输入过长→LLMContextLengthError（不重试，提示用户缩短语音输入或重新开始语音会话）、llmgateway 配额耗尽→LLMQuotaExceededError（不重试，提示联系管理员）、不可恢复错误→ExternalServiceError
- [X] T051 [P] 实现多标签页冲突检测：利用 Redis voice:session:{user_id} 检测已有活跃会话 → 新连接时发送 SESSION_CONFLICT error → 前端提示用户关闭其他标签页的语音模式
- [X] T052 [P] 实现 WebSocket 连接断线恢复：客户端断线自动重连一次 → 检查 Redis 会话状态 → 有状态则恢复上游 llmgateway 连接 → 无状态则要求重新 configure
- [X] T053 [P] 前端录音异常处理：麦克风权限被拒绝时友好提示、浏览器切换标签/最小化时暂停录音并提示、网络断开时显示重连状态
- [X] T054 验证 MediaAttachment 过期清理覆盖语音附件（FR-024a）：确认 `clean_expired_media` Celery 任务正确处理 media_type='audio' 的附件、验证 7 天过期策略适用于语音文件、验证手动删除语音记录时同步删除 MinIO 音频文件
- [X] T055 运行 quickstart.md 端到端验证（⚠️ 需运行时验证，代码层面已就绪）：按 `specs/009-voice-interaction/quickstart.md` 流程完整测试 WebSocket 连接 → 语音模式 → AI 回复 → 消息持久化 → 声纹注册 → 设备管理 → 语音设置
- [X] T055b [P] 验证前端打包大小（宪法 5.2 合规）：运行 `npm run build` 后检查 gzip 首次加载包体积是否仍 < 200KB。AudioWorklet processor（PCM16 采集脚本）MUST 通过 `new URL('./processor.js', import.meta.url)` 独立加载，不纳入主 bundle。若超限则对语音组件启用 `next/dynamic` 动态导入拆分 chunk
- [X] T055a [P] 实现语音端点频率限制（宪法 4.1 合规）：WebSocket 连接频率限制（认证用户 10 次/分，防止重复连接风暴）、语音 REST API 频率限制（复用现有 DRF throttle 机制：匿名 100 次/时、认证 1000 次/时）、声纹注册特殊限制（5 次/时，防止滥用 llmgateway 资源）、**语音触发 LLM 推理频率限制（60 次/分/用户，宪法 4.1 "大模型 60 次/分"条款）**：在 `voice_session_service.py` 中每次向 llmgateway 发送 `input.commit` 或 `auto_respond` 触发推理前，检查 Redis 计数器 `voice:llm_rate:{user_id}` (TTL=60s)，超限时向客户端发送 LLMRateLimitError 提示等待。在 `backend/apps/voice/views.py` 配置 DRF throttle_classes，在 `backend/apps/voice/consumers.py` connect 方法中实现 WebSocket 连接频率检查

---

## Dependencies & Execution Order (Legacy — 完整版本见 Phase 9 后的 Updated 版本)

*参见 Phase 9 后的 "Dependencies & Execution Order (Updated)" 部分*

### User Story Dependencies

- **US1 (P1)**: Phase 2 完成后即可开始 — **MVP 核心**
- **US2 (P2)**: 依赖 US1（WebSocket 消费者 + 前端语音模式）
- **US4 (P4)**: 依赖 US1（WebSocket 基础设施 + 消息持久化流程）
- **US5 (P5)**: 依赖 US4（声纹识别 + 用户身份归属）
- **US6 (P6)**: 依赖 US1（消息持久化）— **可与 US2/US4 并行**

### Within Each User Story

1. Models → Repositories → Services → Views/Consumers → Frontend Hooks → Frontend Components → Integration
2. 后端先行 → 前端跟进
3. 每个用户故事完成后为独立可测试增量

### Parallel Opportunities

**Phase 2 内部**:
- T007, T008 可并行（不同文件）
- T010, T011, T012, T013 可并行
- T014, T015, T016 可并行

**Phase 3 (US1) 内部**:
- T017, T018 可并行（不同服务文件）
- T020, T021, T023 可并行（不同前端文件）

**跨用户故事**:
- Phase 4 (US2) 与 Phase 7 (US6) 可并行（无交叉依赖）

---

## Parallel Example: User Story 1

```bash
# 后端服务并行（不同文件，无依赖）:
Agent A: "T017 实现 gateway_client.py"
Agent B: "T018 实现 voice_session_service.py"

# → T019 consumers.py 依赖 T017+T018 完成

# 前端 Hooks 并行（不同文件）:
Agent A: "T020 实现 usePCMAudioCapture.ts"
Agent B: "T021 实现 useVoiceWebSocket.ts"
Agent C: "T023 创建 VoiceWaveform.tsx"

# → T022 useVoiceMode.ts 依赖 T020+T021
# → T024 VoiceModePanel.tsx 依赖 T022+T023
```

---

## Implementation Strategy

### MVP First (仅 User Story 1)

1. 完成 Phase 1: Setup — 安装依赖、配置环境
2. 完成 Phase 2: Foundational — 模型迁移、路由注册
3. 完成 Phase 3: User Story 1 — 语音模式核心
4. **STOP and VALIDATE**: 测试 WebSocket 连接 → 录音 → AI 文字回复 → 消息保存
5. 可部署/演示 MVP

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. + US1 → 语音模式可用 → **Deploy MVP!**
3. + US2 → 打断能力 → Deploy
4. + US6 → 语音记忆 + 播放器 → Deploy（可与步骤 3 并行）
5. + US4 → 声纹 + 设备管理 → Deploy
6. + US5 → 智能响应决策 → Deploy
7. + Polish → 降级/安全/验证 → Final Deploy
8. + Tests → 全面测试覆盖 → Release

---

## Phase 9: Testing（测试覆盖）

**Purpose**: 满足宪法 3.1 测试覆盖率要求（服务层 95%、总体 80%+），确保所有服务层、数据访问层、模型层和视图层的测试覆盖

**⚠️ 宪法合规**: 本阶段为宪法 3.1 强制要求，不可跳过

**Depends on**: 所有用户故事实现完成（Phase 3-7）

### 后端测试

- [X] T056 [P] 编写 Voice 模型单元测试 `backend/tests/voice/test_models.py`：SpeakerProfile 模型（OneToOne 约束、gateway_speaker_id 唯一性、级联删除）、RegisteredDevice 模型（token_prefix 索引、device_uuid 唯一性、is_active 默认值）、VoiceSettings 模型（JSONField 默认值、recording_mode choices 验证、vad_sensitivity 范围验证）、Message 扩展字段（is_voice 默认值、speaker_id nullable）。目标覆盖率 ≥ 90%
- [X] T057 [P] 编写数据访问层测试 `backend/tests/voice/test_repositories.py`：SpeakerProfileRepository（按 gateway_speaker_id 查找、按 user_id 查找/删除、不存在时返回 None）、RegisteredDeviceRepository（按 token_prefix 查找、按 user_id 查活跃设备列表、撤销设备）、VoiceSettingsRepository（get_or_create 行为、更新设置）。使用 pytest-django 真实数据库。目标覆盖率 ≥ 85%
- [X] T058 [P] 编写声纹服务测试 `backend/tests/voice/test_speaker_service.py`：register_speaker（mock llmgateway HTTP 调用 → 验证 SpeakerProfile 创建）、delete_speaker（验证本地删除 + mock llmgateway DELETE 调用）、identify_speaker（gateway_speaker_id 已注册→返回 user_id、未注册→返回 None）、list_speakers。Mock httpx 外部调用。目标覆盖率 ≥ 95%
- [X] T059 [P] 编写设备管理服务测试 `backend/tests/voice/test_device_service.py`：register_device（验证 UUID 生成 + SM4 加密 + token_prefix 存储 + 明文 Token 仅返回一次）、revoke_device（验证 is_active=False）、authenticate_by_token（正确 Token 认证成功 + 更新 last_active_at、错误 Token 认证失败、已撤销设备认证失败）、list_devices。目标覆盖率 ≥ 95%
- [X] T060 [P] 编写响应决策服务测试 `backend/tests/voice/test_response_decision.py`：decide() 方法全路径测试 — 唤醒词精确匹配→RESPOND、模糊匹配→RESPOND、紧急命令词→STOP、活跃对话内无唤醒词→RESPOND、非活跃对话无唤醒词→RECORD_ONLY、多因素句式判断（问句特征）、自定义唤醒词加载、活跃对话超时行为。Mock Redis 和 VoiceSettings。目标覆盖率 ≥ 95%
- [X] T061 [P] 编写语音会话服务测试 `backend/tests/voice/test_voice_session.py`：Redis 会话状态创建/读取/删除、单会话强制（新会话覆盖旧会话）、TTL 过期验证、活跃对话标记设置与过期、音频帧缓存累积、消息持久化（创建 Message + MediaAttachment + assistant Message）、音频文件 MinIO 上传。Mock Redis 和 MinIO。目标覆盖率 ≥ 95%
- [X] T062 编写 WebSocket 消费者测试 `backend/tests/voice/test_consumers.py`：使用 channels.testing.WebsocketCommunicator。测试场景：Cookie 认证成功/失败、API Token 认证成功/失败、session.configure 处理（voice_chat + continuous_listen 模式）、Binary 帧透传到 llmgateway（mock gateway_client）、llmgateway 事件转发到客户端（response.delta 嵌套 delta.content 结构、response.end 含 response_id + input_tokens/output_tokens）、session.close 清理、连接断开清理、response.cancel 打断处理（携带 response_id，cancel 后无 response.end 需主动清理）、speaker.identified 事件增强（解析 identified 布尔字段 + 添加 user_id/user_name）、SESSION_CONFLICT 错误、异步 STT 转写触发与 transcription.complete 事件生成、WebSocketTokenAuthMiddleware 认证测试（Cookie 有效/过期/缺失/SM4 解密失败）。目标覆盖率 ≥ 80%
- [X] T063 [P] 编写 REST API 视图测试 `backend/tests/voice/test_views.py`：SpeakerListCreateView（GET 列表 + POST 注册 multipart）、SpeakerDeleteView（DELETE 成功 + 404）、DeviceListCreateView（GET + POST）、DeviceDeleteView（DELETE 成功 + 404）、VoiceSettingsView（GET get_or_create + PUT 更新）。验证认证要求、响应格式（宪法 1.2 统一格式）、权限控制。目标覆盖率 ≥ 80%
- [X] T064 [P] 编写 llmgateway 客户端测试 `backend/tests/voice/test_gateway_client.py`：WebSocket 连接建立/断开、Binary 帧发送、JSON 事件接收与分发、session.configure 发送、心跳保活、连接断开自动重连（成功/失败场景）、宪法 4.3 异常映射（连接失败→LLMConnectionError、超时→LLMTimeoutError）。Mock websockets 库。目标覆盖率 ≥ 95%
- [X] T064a [P] 编写端到端延迟基准测试 `backend/tests/voice/test_latency_benchmark.py`：使用 channels.testing.WebsocketCommunicator 模拟完整语音流程（session.configure → 发送 PCM16 音频帧 → mock llmgateway response.start），测量从最后一帧音频发送到收到 response.start 的时间间隔，断言 < 5s（SC-001）。Mock llmgateway 延迟模拟（网络延迟 + 推理延迟）。此为基准测试非 CI 必跑项

### 前端测试

- [X] T065 [P] 编写前端语音 Hook 测试 `frontend/src/hooks/__tests__/`：useVoiceMode（状态流转测试 idle→configuring→listening→processing→responding→idle）、useVoiceWebSocket（连接/断开/事件分发 mock）、usePCMAudioCapture（AudioContext mock + 30ms 帧输出）、voiceApi.ts（REST API 调用层 mock 测试：声纹 CRUD、设备 CRUD、设置 GET/PUT）。使用 Jest + React Testing Library。目标覆盖率 ≥ 85%
- [X] T066 [P] 编写前端语音组件测试：VoiceModePanel（录音按钮交互 + 状态显示）、VoiceMessageBubble（转写文字显示 + 播放器渲染）、VoiceWaveform（Canvas 渲染）。使用 Jest + React Testing Library。目标覆盖率 ≥ 75%

**Checkpoint**: 测试覆盖完整 — 后端服务层 ≥ 95%、数据层 ≥ 85%、视图层 ≥ 80%、前端 Hooks ≥ 85%、前端组件 ≥ 75%

---

## Dependencies & Execution Order (Updated)

### Phase Dependencies

```
Phase 1: Setup ─────────────────────────► 无依赖，立即开始
     │
     ▼
Phase 2: Foundational ─────────────────► 依赖 Phase 1 完成
     │
     ▼
Phase 3: US1 语音模式 (P1) 🎯 MVP ────► 依赖 Phase 2 完成
     │
     ├──► Phase 4: US2 流式打断 (P2) ──► 依赖 Phase 3 (WebSocket 基础)
     │
     ├──► Phase 7: US6 语音记忆 (P6) ──► 依赖 Phase 3 (消息持久化)
     │
     ▼
Phase 5: US4 声纹与设备 (P4) ──────────► 依赖 Phase 3 (WebSocket + 消息归属)
     │
     ▼
Phase 6: US5 响应决策 (P5) ────────────► 依赖 Phase 5 (声纹识别能力)
     │
     ▼
Phase 8: Polish ───────────────────────► 依赖所有用户故事完成
     │
     ▼
Phase 9: Testing ──────────────────────► 依赖所有功能实现完成（可与 Phase 8 部分并行）
```

---

## Notes

- 总任务数: **71** 个
- US1 (MVP): **10** 个任务
- US2 (打断): **4** 个任务
- US4 (声纹+设备): **10** 个任务
- US5 (响应决策): **6** 个任务
- US6 (语音记忆): **3** 个任务
- Setup: **8** 个（含 T005a, T006a）| Foundational: **10** 个 | Polish: **8** 个（含 T055a, T055b）| Testing: **12** 个（含 T064a）
- 可并行任务: **44** 个（标记 [P] 的任务）
- 建议 MVP 范围: Phase 1 + Phase 2 + Phase 3（共 **28** 个任务）
