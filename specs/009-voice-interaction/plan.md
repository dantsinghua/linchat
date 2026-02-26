# Implementation Plan: 语音交互

**Branch**: `009-voice-interaction` | **Date**: 2026-02-14 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/009-voice-interaction/spec.md`

## Summary

为 LinChat 添加实时语音交互能力。核心架构：LinChat 作为 WebSocket 代理层，客户端（Web 浏览器/外部设备）连接 LinChat WebSocket 端点，LinChat 转发音频到 llmgateway WebSocket 上游，同时处理认证、声纹匹配表查询、消息持久化、响应决策等业务逻辑。

技术路线：引入 Django Channels 处理 WebSocket 连接，前端使用 Web Audio API (AudioWorklet) 采集 PCM16 实时音频流，新增 `apps/voice/` Django 应用承载语音特有模型和服务。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**:
- 后端新增: `channels>=4.0` (WebSocket 消费者), `channels-redis>=4.0` (Redis channel layer), `websockets>=12.0` (上游 llmgateway WebSocket 客户端), `httpx` (已有依赖，语音功能复用于异步 HTTP STT 转写调用), `pypinyin>=0.51` (唤醒词拼音模糊匹配)
- 前端新增: 无新包依赖，使用浏览器原生 Web Audio API + AudioWorklet

**重要**: LinChat 自行实现异步 STT 转写（通过 HTTP 调用 MiniCPM-o `POST /v1/chat/completions`），llmgateway WebSocket 不提供独立转写事件。声纹注册 HTTP 端点仅接受 WAV (PCM16, 16kHz, mono) 格式。

**Storage**: PostgreSQL 15 (新模型: SpeakerProfile, RegisteredDevice, VoiceSettings + Message 扩展字段), Redis (语音会话状态/channel layer), MinIO (音频文件)
**Testing**: pytest + pytest-django + pytest-asyncio (后端), Jest (前端)
**Target Platform**: Linux server (Ubuntu 20.04+)
**Project Type**: Web application (前后端分离)
**Performance Goals**: 语音录音结束到 AI 文字回复首 token < 5 秒 (SC-001), WebSocket 连接建立 < 500ms (宪法 5.1)
**Constraints**: 单用户单语音会话 (FR-034), PCM16 16kHz mono 音频格式 (FR-008), 单次录音 ≤ 30 秒 (FR-007)
**Scale/Scope**: 家庭场景单用户系统，同一时间仅一个语音交互

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 检查项 | 状态 | 说明 |
|------|--------|------|------|
| 1.1 关注点分离 | 新增 apps/voice/ 遵循视图→服务→数据层 | ✅ PASS | consumers.py 仅处理 WebSocket 协议，业务逻辑在 services/ |
| 1.2 接口设计 | WebSocket /ws/voice/ + REST /api/v1/voice/ | ✅ PASS | WebSocket 用于双向实时流，REST 用于声纹/设备/设置 CRUD |
| 1.3 数据一致性 | PostgreSQL 为主，Redis 存瞬态会话 | ✅ PASS | 语音消息写入 Message 表保证原子性 |
| 2.1 Python 规范 | 类型注解 + Google 文档字符串 | ✅ PASS | 所有公共函数强制 |
| 2.2 TypeScript 规范 | 严格模式 + interface Props | ✅ PASS | 新增 voice.ts 类型定义 |
| 3.1 测试覆盖 | 服务层 95%，总体 80%+ | ✅ PASS | 计划编写完整测试套件 |
| 4.1 认证 | Cookie (Web) + API Token (设备) | ✅ PASS | 复用 httpOnly Cookie，设备 Token SM4 加密 |
| 4.2 数据保护 | SM4 加密设备 Token | ✅ PASS | 复用现有加密工具 |
| 4.3 LLM 异常处理 | llmgateway 错误映射 | ✅ PASS | WebSocket error 事件含 recoverable 字段 |
| 4.4 术语 | user_id 粒度隔离 | ✅ PASS | 所有操作按 user_id 隔离 |
| 5.1 性能 | WebSocket < 500ms, 语音首 token < 5s | ✅ PASS | 符合多模态推理首字节 < 5s 豁免 |
| 8.2 ASGI | uvicorn + Channels ProtocolTypeRouter | ✅ PASS | Channels 兼容 uvicorn，不需要 daphne；使用自定义 WebSocketTokenAuthMiddleware 替代 AuthMiddlewareStack（兼容 SM4 Token-in-Cookie 认证） |
| 9.2 单用户 | 同一时间一个语音交互 | ✅ PASS | FR-034 强制单会话 |

## Project Structure

### Documentation (this feature)

```text
specs/009-voice-interaction/
├── plan.md                              # 本文件
├── research.md                          # Phase 0: 技术决策
├── data-model.md                        # Phase 1: 数据模型设计
├── quickstart.md                        # Phase 1: 快速上手指南
├── contracts/
│   ├── websocket-protocol.md            # LinChat ↔ 客户端 WebSocket 协议
│   ├── voice-rest-api.yaml              # 语音 REST API 契约
│   └── llmgateway-integration.md        # llmgateway 集成参考
└── tasks.md                             # Phase 2: 任务清单 (由 /speckit.tasks 生成)
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── chat/
│   │   ├── models.py                    # EXTEND: Message 新增 is_voice, speaker_id 字段
│   │   ├── serializers.py               # EXTEND: 语音消息序列化字段
│   │   └── migrations/
│   │       └── 0005_message_voice_fields.py  # NEW: 语音字段迁移（0004 已被 remove_thumbnail_add_document_type 占用）
│   │
│   ├── common/
│   │   └── websocket_auth.py            # NEW: WebSocket Token 认证中间件（替代 AuthMiddlewareStack）
│   │
│   └── voice/                           # NEW APP
│       ├── __init__.py
│       ├── apps.py
│       ├── models.py                    # SpeakerProfile, RegisteredDevice, VoiceSettings
│       ├── serializers.py               # DRF 序列化器
│       ├── views.py                     # REST API 视图 (声纹/设备/设置)
│       ├── urls.py                      # URL 路由
│       ├── consumers.py                 # WebSocket 消费者 (语音代理)
│       ├── routing.py                   # WebSocket URL 路由
│       ├── services/
│       │   ├── __init__.py
│       │   ├── voice_session_service.py # 语音会话生命周期
│       │   ├── speaker_service.py       # 声纹注册/匹配 + llmgateway 对接
│       │   ├── response_decision_service.py  # 唤醒词/响应决策
│       │   ├── device_service.py        # 设备注册/Token 管理
│       │   └── gateway_client.py        # llmgateway WebSocket 客户端
│       ├── repositories.py              # 数据访问层
│       ├── migrations/
│       │   ├── 0001_initial.py          # SpeakerProfile, RegisteredDevice, VoiceSettings
│       │   └── 0002_create_unknown_user.py  # 预创建 unknown 用户
│       └── admin.py
│
├── core/
│   ├── asgi.py                          # EXTEND: ProtocolTypeRouter (HTTP + WebSocket via WebSocketTokenAuthMiddleware)
│   └── settings.py                      # EXTEND: INSTALLED_APPS + CHANNEL_LAYERS
│
└── tests/
    └── voice/                           # NEW
        ├── __init__.py
        ├── test_consumers.py            # WebSocket 消费者测试
        ├── test_voice_session.py        # 会话服务测试
        ├── test_speaker_service.py      # 声纹服务测试
        ├── test_response_decision.py    # 响应决策测试
        ├── test_device_service.py       # 设备管理测试
        ├── test_models.py               # 模型测试
        ├── test_views.py                # REST API 测试
        ├── test_repositories.py         # 数据访问测试
        ├── test_gateway_client.py       # llmgateway 客户端测试
        └── test_latency_benchmark.py    # 端到端延迟基准测试

frontend/
├── src/
│   ├── app/
│   │   └── settings/
│   │       └── page.tsx                 # EXTEND: 语音设置/声纹管理/设备管理区域
│   │
│   ├── components/
│   │   ├── chat/
│   │   │   ├── MessageList.tsx          # EXTEND: 语音消息气泡渲染
│   │   │   └── MessageInput.tsx         # EXTEND: 语音模式切换按钮
│   │   │
│   │   ├── voice/                       # NEW
│   │   │   ├── VoiceModePanel.tsx       # 底部语音控制面板
│   │   │   ├── VoiceWaveform.tsx        # 实时音频波形可视化
│   │   │   └── VoiceMessageBubble.tsx   # 语音消息展示 (转写文字 + 播放器)
│   │   │
│   │   └── settings/
│   │       ├── VoiceSettingsCard.tsx     # NEW: 语音偏好设置
│   │       ├── SpeakerProfileCard.tsx   # NEW: 声纹注册管理
│   │       └── DeviceManageCard.tsx     # NEW: 设备注册管理
│   │
│   ├── hooks/
│   │   ├── useVoiceMode.ts             # NEW: 语音模式状态机
│   │   ├── useVoiceWebSocket.ts        # NEW: WebSocket 连接管理
│   │   └── usePCMAudioCapture.ts       # NEW: AudioWorklet PCM16 采集
│   │
│   ├── services/
│   │   └── voiceApi.ts                 # NEW: 语音 REST API 调用
│   │
│   ├── stores/
│   │   └── voiceStore.ts              # NEW: 语音全局状态 (Zustand) — currentTranscription 由 LinChat 自行生成的 transcription.complete 事件驱动
│   │
│   └── types/
│       └── voice.ts                    # NEW: 语音类型定义
```

**Structure Decision**: 采用 Web application 双端结构。后端新增 `apps/voice/` 独立应用（≥4 个服务类，使用 services/ 目录模式），扩展现有 `apps/chat/` 的 Message 模型。前端新增 `components/voice/` 和 `hooks/` 目录。

## Complexity Tracking

| 澄清项 | 说明 | 与宪法关系 |
|---------|------|-----------|
| 多用户声纹识别 vs 单用户系统 | 声纹识别用于共享设备场景（树莓派），识别"谁在说话"并将消息归属到正确的 user_id。系统仍然同一时间只处理一个语音交互，符合宪法 9.2 单用户约束 | 不违反 — 声纹识别是用户身份归属，不是并发控制 |
| 新增 Django Channels 依赖 | WebSocket 双向实时流是语音交互的硬需求，SSE 单向流无法满足。Channels 与 uvicorn 兼容 | 不违反 8.2 — uvicorn 仍为 ASGI 服务器 |
