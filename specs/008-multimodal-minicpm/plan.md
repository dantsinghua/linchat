# Implementation Plan: 全模态模型接入 (MiniCPM-V/o)

**Branch**: `008-multimodal-minicpm` | **Date**: 2026-02-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/008-multimodal-minicpm/spec.md`

## Summary

本特性实现 MiniCPM-V/o 多模态模型的接入，支持图像理解、视频分析、语音交互等能力。技术方案基于现有 LangGraph Agent 架构，通过 LLM Gateway 统一调用多模态模型，扩展消息模型支持媒体附件，使用 MinIO 存储媒体文件，Redis 管理推理任务状态。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, LangGraph, LangChain, Pillow (图像处理), ffmpeg-python (视频处理), httpx, redis-py (async), Next.js 14+, React 18+, Zustand
**Storage**: PostgreSQL 15 (主存储, MediaAttachment 元数据), MinIO (媒体文件), Redis (推理任务状态/事件推送)
**Testing**: pytest, pytest-django, pytest-asyncio, Jest, React Testing Library
**Target Platform**: Linux server (后端) + Modern browsers (前端)
**Project Type**: Web application (frontend + backend)
**Performance Goals**: 图片首字节响应 < 5s, 推理取消 < 500ms, 视频处理 < 2倍时长
**Constraints**: 单用户家庭场景，不实现并发控制（宪法 9.2）, 图片 ≤ 10MB, 视频 ≤ 50MB/60s, 文档 ≤ 10MB（Gateway 限制 E6001）
**Timeouts**: 推理 180s, 文档解析创建 30s, 文档解析轮询 30s, 文档解析结果 30s, TTS 60s, 推理取消 5s（共 6 种，参见 FR-032 和 upstream-integration-guide.md §8.2）
**Guardrails**: LLM_GATEWAY_GUARDRAILS_LEVEL=fast（默认，< 10ms 延迟），处理 content_control SSE 事件
**Scale/Scope**: 现有用户基数，媒体文件 7 天自动清理
**Gateway 参考**: docs/upstream-integration-guide.md v2.0.0（LLM Gateway 集成指南）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 状态 | 说明 |
|------|------|------|
| 1.1 关注点分离 | ✅ PASS | 视图层仅处理 HTTP，服务层封装业务逻辑，数据层封装存储操作 |
| 1.2 接口设计标准 | ✅ PASS | RESTful API + SSE 流式响应，复用现有 ASGI 异步视图模式 |
| 1.3 数据一致性 | ✅ PASS | PostgreSQL 为主存储，Redis 仅存临时状态，MinIO 存媒体文件 |
| 2.1 Python 规范 | ✅ PASS | 类型注解、Google 文档风格、Black + isort |
| 2.2 TypeScript 规范 | ✅ PASS | 严格模式、Props interface、ESLint + Prettier |
| 3.1 测试覆盖率 | ✅ PASS | 服务层 95%、总体 80%+ |
| 4.1 安全要求 | ✅ PASS | user_id 数据隔离、Token httpOnly Cookie、媒体文件所有权校验 |
| 4.3 LLM 异常处理 | ✅ PASS | 复用现有异常 + Gateway 错误格式映射：网络连接失败(httpx ConnectionError)→LLMConnectionError 重试3次, 网络超时(httpx TimeoutError)→LLMTimeoutError 重试3次, E3002 模型不可用→ExternalServiceError(503) 不重试（含 retry_after，对齐 spec.md"不实现自动模型降级"）, E3003 推理超时→LLMTimeoutError 重试3次, E1002/E2004→LLMRateLimitError 不重试, E4001-E4005→LLMContentFilterError 不重试, E3004→LLMContextLengthError 不重试, E5004→LLMQuotaExceededError 不重试 |
| 4.4 术语定义 | ✅ PASS | 按 user_id 粒度隔离（非会话粒度） |
| 5.1 响应时间 | ✅ PASS | 图片首字节 < 5s，中断 < 500ms |
| 8.2 ASGI 服务器 | ✅ PASS | 使用 uvicorn，禁止 runserver |

## Project Structure

### Documentation (this feature)

```text
specs/008-multimodal-minicpm/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── media-upload.yaml    # 媒体上传接口
│   ├── multimodal-chat.yaml # 多模态聊天接口
│   ├── inference-cancel.yaml # 推理取消接口
│   ├── document-parse.yaml  # 文档解析接口（透传 Gateway）
│   └── tts.yaml             # TTS 语音合成接口
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── chat/
│   │   ├── models.py           # 扩展 Message 模型 + 新增 MediaAttachment
│   │   ├── serializers.py      # 媒体上传序列化器
│   │   ├── views.py            # 媒体上传视图 + 推理取消视图
│   │   ├── services/
│   │   │   ├── media_service.py    # 媒体文件处理服务（上传/过期清理）
│   │   │   ├── minio_service.py   # MinIO 对象存储操作服务
│   │   │   ├── inference_service.py # 推理任务管理服务（取消/状态追踪）
│   │   │   ├── document_parse_service.py # 文档解析服务（透传 Gateway API）
│   │   │   └── tts_service.py      # TTS 语音合成服务
│   │   ├── repositories.py     # MediaAttachment 数据访问
│   │   └── tasks.py            # Celery 定时任务（媒体过期清理）
│   ├── graph/
│   │   ├── agent.py            # 扩展支持多模态消息格式
│   │   └── services/
│   │       └── agent_service.py # 扩展支持多模态推理
│   └── common/
│       ├── event_service.py    # 扩展 EventType 支持 INFERENCE_CANCEL + DOC_PARSE_PROGRESS
│       └── gateway_utils.py    # Gateway 通用工具（retry 装饰器 + 请求头注入）
└── tests/
    └── chat/
        ├── test_media_service.py
        ├── test_minio_service.py
        ├── test_inference_service.py
        ├── test_inference_cancel.py
        ├── test_media_views.py
        ├── test_document_parse_service.py
        ├── test_document_parse_views.py
        ├── test_video_processing.py
        ├── test_audio_processing.py
        ├── test_tts_service.py
        ├── test_tts_views.py
        ├── test_model_routing.py
        ├── test_media_attachment_repo.py
        └── test_media_cleanup_task.py

frontend/
├── src/
│   ├── components/
│   │   └── chat/
│   │       ├── MessageInput.tsx      # 扩展支持媒体上传
│   │       ├── MediaUploader.tsx     # 媒体上传组件（预览/进度）
│   │       ├── MediaPreview.tsx      # 媒体预览组件（图片/视频/音频）
│   │       ├── AudioRecorder.tsx     # 语音录制组件
│   │       ├── AudioPlayer.tsx       # 语音播放组件（播放/暂停/进度）
│   │       └── MessageList.tsx       # 扩展支持媒体消息渲染
│   ├── assets/
│   │   └── placeholders/        # 媒体类型静态 SVG 占位图
│   │       ├── image-placeholder.svg
│   │       ├── video-placeholder.svg
│   │       ├── audio-placeholder.svg
│   │       └── document-placeholder.svg
│   ├── hooks/
│   │   ├── useAudioRecorder.ts  # 录音 Hook
│   │   └── useDocParse.ts       # 文档解析进度 Hook
│   ├── services/
│   │   ├── mediaApi.ts         # 媒体上传 API + 推理控制 API
│   │   └── ttsApi.ts           # TTS 语音合成 API
│   ├── stores/
│   │   └── uploadStore.ts      # 上传状态管理
│   └── types/
│       └── media.ts            # 媒体相关类型定义
└── tests/
    ├── components/
    │   └── chat/
    │       ├── MediaUploader.test.tsx
    │       ├── MediaPreview.test.tsx
    │       ├── AudioRecorder.test.tsx
    │       └── AudioPlayer.test.tsx
    ├── hooks/
    │   └── useDocParse.test.ts
    ├── services/
    │   └── mediaApi.test.ts
    └── e2e/
        ├── multimodal-image.spec.ts
        ├── inference-cancel.spec.ts
        └── voice-interaction.spec.ts
```

**Structure Decision**: 采用 Option 2 Web application 结构，扩展现有 chat 模块，新增媒体处理相关服务和组件。

## Complexity Tracking

无宪法违规需要说明。

## Phase 0: Research Summary

详见 [research.md](./research.md)

### Key Decisions

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 媒体存储 | MinIO | 已有部署，支持 S3 兼容 API |
| 推理任务状态 | Redis 临时存储 | 复用 EventService 机制，无需持久化 |
| ~~并发控制~~ | ~~已移除~~ | ~~单用户家庭场景不需要（宪法 9.2）~~ |
| 多模态消息格式 | OpenAI 兼容格式 | 网关已实现，直接透传 |
| Gateway 错误格式映射 | 后端统一转换 | Gateway `{"error":{"code":"Exxxx",...}}` → LinChat `{"code":"...","data":{"gateway_error":"Exxxx",...}}` |
| 模型不可用降级 | 不降级，报错 | minicpm-v/o 不可用时返回 503，不回退到文本模型（避免"发了图片但 AI 看不到"的混淆体验） |
| TTS/Cancel 端点 | 预留 + 降级 | `/v1/audio/speech` 和 `/v1/chat/cancel` 尚未在 upstream-integration-guide.md v2.0.0 中列出，后端实现降级逻辑 |
| Guardrails 级别 | fast（默认） | < 10ms 延迟，不影响流式响应体验；处理 `content_control` SSE 事件用于安全过滤 |
| X-Request-ID | 透传 Gateway 请求头 | 用于跨系统请求追踪，Gateway 侧错误响应中返回对应 request_id |

## Phase 1: Design Artifacts

### 1.1 Data Model

详见 [data-model.md](./data-model.md)

**新增实体**:
- `MediaAttachment`: 媒体文件元数据（扩展 chat.models）
- `InferenceTask`: Redis 临时状态（非数据库实体）

**扩展实体**:
- `Message.attachments`: 关联媒体附件
- `EventType.INFERENCE_CANCEL`: 新增事件类型
- `EventType.DOC_PARSE_PROGRESS`: 新增事件类型

### 1.2 API Contracts

详见 [contracts/](./contracts/) 目录

Gateway 集成约束参见 spec.md Gateway API Contract 节。

**新增端点**:
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/chat/media/upload/` | 上传媒体文件 |
| GET | `/api/v1/chat/media/{uuid}/` | 获取媒体文件 |
| POST | `/api/v1/chat/inference/cancel/` | 取消推理任务 |
| POST | `/api/v1/chat/documents/parse/` | 创建文档解析任务（透传 Gateway） |
| GET | `/api/v1/chat/documents/tasks/{task_id}/` | 查询文档解析任务状态 |
| GET | `/api/v1/chat/documents/tasks/{task_id}/result/` | 获取文档解析结果（Markdown/JSON） |
| POST | `/api/v1/chat/tts/` | 获取 AI 回复的语音合成（透传 Gateway） |

**扩展端点**:
| 方法 | 路径 | 变更 |
|------|------|------|
| POST | `/api/v1/chat/` | 支持 attachments 参数 |

### 1.3 Quickstart Guide

详见 [quickstart.md](./quickstart.md)

## Phase 2: Task Generation

使用 `/speckit.tasks` 命令生成任务清单。Gateway 错误码映射参见 spec.md Gateway API Contract 节。
