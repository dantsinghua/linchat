# Feature 008: 全模态模型接入 (MiniCPM-V/o)

> 状态: **已完成** — 已合并到 main

## 特性概述

接入全模态模型 MiniCPM-V/o，支持图像理解、视频分析、语音交互等多模态能力。通过 LLM Gateway 统一访问私有化部署的 MiniCPM 模型服务（OpenAI 兼容 API）。核心能力包括：图像理解对话、中途停止推理（cancel_inference）、文档解析（PDF/DOCX -> Markdown）、视频内容分析、语音输入识别（ASR）。媒体文件通过 MinIO 对象存储管理。

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范（6 个用户故事：图像理解/推理取消/文档解析/视频分析/语音输入/语音回复） |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单（按 US1-US6 分组，服务层覆盖率 95%+） |
| `data-model.md` | 数据模型（MediaAttachment 模型、媒体文件生命周期） |
| `research.md` | 技术调研（MiniCPM-V/o 能力、vLLM 视频支持、音频处理方案） |
| `quickstart.md` | 快速入门 |

## API 契约

| 契约文件 | 端点 | 说明 |
|---------|------|------|
| `contracts/multimodal-chat.yaml` | POST /api/v1/chat/ | 多模态聊天消息（含 attachments） |
| `contracts/media-upload.yaml` | POST /api/v1/chat/media/upload/ | 媒体文件上传（multipart/form-data） |
| `contracts/inference-cancel.yaml` | POST /api/v1/chat/inference/cancel/ | 推理任务取消 |
| `contracts/document-parse.yaml` | POST /api/v1/chat/documents/parse/ | 文档解析任务（PDF/DOCX -> Markdown） |
| `contracts/tts.yaml` | POST /api/v1/chat/tts/ | TTS 语音合成（返回 audio/mpeg） |

Gateway 集成契约以 `docs/upstream-integration-guide.md` 为最终权威来源。

## 检查清单

| 文件 | 内容 |
|------|------|
| `checklists/requirements.md` | 规范质量检查清单 |

## 相关代码位置

| 模块 | 路径 | 职责 |
|------|------|------|
| 媒体服务 | `backend/apps/chat/services/media_service.py` | 媒体上传/下载/过期清理 |
| 推理服务 | `backend/apps/chat/services/inference_service.py` | 多模态推理 + 推理取消 |
| 上下文服务 | `backend/apps/chat/services/context_service.py` | 文档解析集成 |
| 服务类型 | `backend/apps/chat/services/types.py` | 服务层数据结构 |
| 聊天仓储 | `backend/apps/chat/repositories.py` | 媒体附件数据访问 |
| 聊天视图 | `backend/apps/chat/views.py` | REST API 端点（上传/取消/解析/TTS） |
| SSE 视图 | `backend/apps/chat/sse.py` | 流式推理 SSE 端点 |
| 定时任务 | `backend/apps/chat/tasks.py` | Celery 媒体过期清理任务 |
| 多模态 SubAgent | `backend/apps/graph/subagents/multimodal_agent.py` | 多模态推理 SubAgent |
| Agent 主流程 | `backend/apps/graph/agent.py` | 多模态直连 Gateway 推理 |
| 模型配置 | `backend/apps/models/models.py` | LLMModel 多模态字段 |
| 前端组件 | `frontend/src/components/chat/` | MediaUploader/AudioRecorder/AudioPlayer/MediaPreview |
