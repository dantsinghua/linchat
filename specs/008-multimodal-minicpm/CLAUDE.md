# Feature 008: 多模态 MiniCPM 集成

> 状态: **已完成** — 已合并到 main

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范 |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单 |
| `data-model.md` | 数据模型（MediaAttachment） |
| `research.md` | 技术调研（MiniCPM-v/o、vLLM 视频支持） |
| `quickstart.md` | 快速入门 |
| `contracts/` | API 契约（5 个 YAML 文件） |
| `checklists/requirements.md` | 需求检查清单 |

## API 契约

| 契约文件 | 端点 |
|---------|------|
| `multimodal-chat.yaml` | POST /api/v1/chat/ (多模态消息) |
| `media-upload.yaml` | POST /api/v1/chat/media/upload/ |
| `inference-cancel.yaml` | POST /api/v1/chat/inference/cancel/ |
| `document-parse.yaml` | POST /api/v1/chat/documents/parse/ |
| `tts.yaml` | POST /api/v1/chat/tts/ |

## 实现模块

- 后端: `apps/chat/`（媒体上传/TTS/文档解析）+ `apps/graph/agent.py`（多模态直连 Gateway）
- 前端: `components/chat/`（MediaUploader/AudioRecorder/AudioPlayer/MediaPreview）
