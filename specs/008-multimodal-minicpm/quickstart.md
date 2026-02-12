# Quickstart: 全模态模型接入 (MiniCPM-V/o)

**Feature Branch**: `008-multimodal-minicpm`
**Date**: 2026-02-06

## 1. 环境准备

### 1.1 依赖安装

```bash
# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 后端依赖
cd /home/dantsinghua/work/linchat/backend
pip install pillow ffmpeg-python minio

# 前端依赖
cd /home/dantsinghua/work/linchat/frontend
npm install
```

### 1.2 系统依赖

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# 验证安装
ffmpeg -version
```

### 1.3 MinIO Bucket 初始化

```bash
# 使用 MinIO Client (mc)
mc alias set linchat http://localhost:9010 $MINIO_ACCESS_KEY $MINIO_SECRET_KEY

# 创建 Bucket
mc mb linchat/linchat-media

# 验证
mc ls linchat
```

### 1.4 环境变量

在 `backend/.env` 中添加：

```bash
# MinIO 配置
MINIO_ENDPOINT=localhost:9010
MINIO_ACCESS_KEY=your_access_key
MINIO_SECRET_KEY=your_secret_key
MINIO_BUCKET_MEDIA=linchat-media

# LLM Gateway 配置（参见 FR-032 和 tasks.md T003）
LLM_GATEWAY_URL=http://localhost:8080
LLM_GATEWAY_API_KEY=your_gateway_api_key
LLM_GATEWAY_INFERENCE_TIMEOUT=180
LLM_GATEWAY_CANCEL_TIMEOUT=5
LLM_GATEWAY_POLL_TIMEOUT=30
LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT=30
LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT=30
LLM_GATEWAY_TTS_TIMEOUT=60
LLM_GATEWAY_DOC_PARSE_MODEL=minicpm-v
LLM_GATEWAY_GUARDRAILS_LEVEL=fast

# 多模态模型 ID 映射（在 settings.py 中配置）
# 图片/视频 → minicpm-v
# 音频 → minicpm-o
# 纯文本 → 默认模型
```

## 2. 数据库迁移

```bash
cd /home/dantsinghua/work/linchat/backend

# 生成迁移
python manage.py makemigrations chat

# 应用迁移
python manage.py migrate

# 验证
python manage.py showmigrations chat
```

## 3. 启动服务

### 3.1 启动 Docker 服务

```bash
cd /home/dantsinghua/work/linchat
docker compose up -d

# 验证 MinIO
curl http://localhost:9010/minio/health/live
```

### 3.2 启动后端

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 开发模式（带热重载）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload

# 生产模式
uvicorn core.asgi:application --host 0.0.0.0 --port 8002
```

### 3.3 启动前端

```bash
cd /home/dantsinghua/work/linchat/frontend

# 构建
npm run build

# 启动
npm run start -- -p 3784
```

## 4. 功能验证

### 4.1 媒体上传测试

```bash
# 上传图片
curl -X POST http://localhost:8002/api/v1/chat/media/upload/ \
  -H "Cookie: access_token=YOUR_TOKEN" \
  -F "file=@test.jpg"

# 预期响应（参见 contracts/media-upload.yaml）
{
  "code": "SUCCESS",
  "message": "操作成功",
  "data": {
    "attachment_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "media_type": "image",
    "mime_type": "image/jpeg",
    "file_name": "test.jpg",
    "file_size": 102400,
    "width": 800,
    "height": 600,
    "expires_at": "2026-02-13T10:30:00Z"
  }
}
```

### 4.2 多模态聊天测试

```bash
# 发送带图片的消息
curl -X POST http://localhost:8002/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=YOUR_TOKEN" \
  -d '{
    "content": "描述这张图片",
    "attachments": ["550e8400-e29b-41d4-a716-446655440000"]
  }'

# 预期 SSE 响应（参见 contracts/multimodal-chat.yaml）
data: {"type": "content", "content": "这张图片展示了...", "request_id": "xxx"}
data: {"type": "done", "message_id": "xxx", "request_id": "xxx"}
```

### 4.3 推理取消测试

```bash
# 取消推理
curl -X POST http://localhost:8002/api/v1/chat/inference/cancel/ \
  -H "Cookie: access_token=YOUR_TOKEN"

# 预期响应（参见 contracts/inference-cancel.yaml）
{
  "code": "SUCCESS",
  "message": "操作成功",
  "data": {
    "cancelled": true,
    "request_id": "xxx"
  }
}
```

## 5. 测试运行

### 5.1 后端测试

```bash
cd /home/dantsinghua/work/linchat/backend

# 运行全部测试
pytest

# 运行多模态相关测试
pytest tests/chat/test_media_service.py
pytest tests/chat/test_inference_service.py

# 带覆盖率
pytest --cov=apps.chat.services --cov-report=term-missing
```

### 5.2 前端测试

```bash
cd /home/dantsinghua/work/linchat/frontend

# 运行测试
npm test

# 运行特定测试
npm test -- MediaUploader
```

## 6. 常见问题

### 6.1 FFmpeg 未找到

```bash
# 错误信息
FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'

# 解决方案
sudo apt-get install ffmpeg
```

### 6.2 MinIO 连接失败

```bash
# 错误信息
S3Error: Connection refused

# 检查 MinIO 状态
docker compose ps
docker logs linchat-minio

# 确认端口
curl http://localhost:9010/minio/health/live
```

### 6.3 上传文件过大

```bash
# 错误信息
FILE_TOO_LARGE: 文件大小超出限制

# 检查 Nginx 配置
# /etc/nginx/sites-available/deeptutor
client_max_body_size 60m;

# 重载 Nginx
sudo nginx -s reload
```

### 6.4 推理取消

```bash
# 用户手动停止当前推理后再发送新请求（单用户场景，无并发控制）
curl -X POST http://localhost:8002/api/v1/chat/inference/cancel/
```

## 7. 监控与调试

### 7.1 Langfuse 追踪

访问 http://www.greydan.xin:8081 查看多模态推理追踪：
- 按 `multimodal_inference` 名称过滤
- 查看延迟、token 消耗、错误率

### 7.2 Redis 状态查看

```bash
# 连接 Redis
docker exec -it linchat-redis redis-cli

# 查看推理任务
GET user:1:inference_task

# 查看所有推理任务键
KEYS user:*:inference_task
```

### 7.3 MinIO 文件查看

```bash
# 列出媒体文件
mc ls linchat/linchat-media/media/
```

## 8. 关键文件路径

| 文件 | 说明 |
|------|------|
| `backend/apps/chat/models.py` | MediaAttachment 模型 |
| `backend/apps/chat/services/media_service.py` | 媒体文件处理服务 |
| `backend/apps/chat/services/minio_service.py` | MinIO 对象存储服务 |
| `backend/apps/chat/services/inference_service.py` | 推理任务管理服务 |
| `backend/apps/chat/services/document_parse_service.py` | 文档解析服务（透传 Gateway） |
| `backend/apps/chat/services/tts_service.py` | TTS 语音合成服务 |
| `backend/apps/chat/tasks.py` | Celery 定时任务（媒体过期清理） |
| `backend/apps/common/event_service.py` | EventType 扩展 |
| `backend/apps/graph/agent.py` | 多模态消息构建器 |
| `frontend/src/components/chat/MediaUploader.tsx` | 媒体上传组件 |
| `frontend/src/components/chat/MediaPreview.tsx` | 媒体预览组件 |
| `frontend/src/components/chat/AudioRecorder.tsx` | 语音录制组件 |
| `frontend/src/components/chat/AudioPlayer.tsx` | 语音播放组件 |
| `frontend/src/services/mediaApi.ts` | 媒体上传/推理控制 API |
| `frontend/src/services/ttsApi.ts` | TTS 语音合成 API |
