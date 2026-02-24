# 008 contracts — 多模态 API 契约

> 多模态功能的 REST API 契约文档（OpenAPI 3.1 YAML 格式），定义前后端通信协议。

## 契约文件

| 文件 | 端点 | 说明 |
|------|------|------|
| `multimodal-chat.yaml` | POST /api/v1/chat/ | 多模态聊天消息（请求体含 attachments 数组，附带媒体文件引用） |
| `media-upload.yaml` | POST /api/v1/chat/media/upload/ | 媒体文件上传（multipart/form-data，支持图片/视频/音频/文档） |
| `inference-cancel.yaml` | POST /api/v1/chat/inference/cancel/ | 推理任务取消（中断 SSE 流 + 通知 Gateway 终止模型推理） |
| `document-parse.yaml` | POST /api/v1/chat/documents/parse/ | 文档解析任务（PDF/DOCX 转 Markdown，通过 Gateway VL 模型处理） |
| `tts.yaml` | POST /api/v1/chat/tts/ | TTS 语音合成（接收文本，返回 audio/mpeg 音频流） |

## 权威引用

- Gateway 侧的完整 API 规范以 `docs/upstream-integration-guide.md` 为最终权威来源
- 文档解析详细对接指引见 `docs/multimodal-api-guide.md`
