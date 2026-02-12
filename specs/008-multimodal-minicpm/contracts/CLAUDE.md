# 008 contracts 指南

> 多模态功能 API 契约文档（OpenAPI YAML 格式）。

## 契约文件

| 文件 | 端点 | 说明 |
|------|------|------|
| `multimodal-chat.yaml` | POST /api/v1/chat/ | 多模态聊天消息（含 attachments） |
| `media-upload.yaml` | POST /api/v1/chat/media/upload/ | 媒体文件上传（multipart/form-data） |
| `inference-cancel.yaml` | POST /api/v1/chat/inference/cancel/ | 推理任务取消 |
| `document-parse.yaml` | POST /api/v1/chat/documents/parse/ | 文档解析任务 |
| `tts.yaml` | POST /api/v1/chat/tts/ | TTS 语音合成（返回 audio/mpeg） |

## 权威引用

Gateway 集成契约以 `docs/upstream-integration-guide.md` 为最终权威来源。
