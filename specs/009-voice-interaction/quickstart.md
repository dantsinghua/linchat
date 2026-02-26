# Quickstart: 语音交互

**Feature Branch**: `009-voice-interaction`
**Date**: 2026-02-14

## 前置条件

1. Docker 服务运行（PostgreSQL, Redis, MinIO）
2. llmgateway 运行在 `localhost:8888`（WebSocket）和 `localhost:8889`（HTTP）。⚠️ 端口 8081 已被 Langfuse Nginx 占用，不可复用
3. Python 虚拟环境激活

## 后端设置

### 1. 安装新依赖

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

pip install channels>=4.0 channels-redis>=4.0 websockets>=12.0
pip freeze > requirements.txt
```

### 2. 配置

在 `backend/.env` 中添加：

```bash
# llmgateway WebSocket
LLM_GATEWAY_WS_URL=ws://127.0.0.1:8888
LLM_GATEWAY_HTTP_URL=http://127.0.0.1:8889
LLM_GATEWAY_WS_API_KEY=sk-23h8ugn3828910h8g308979y4
```

在 `core/settings.py` 中添加：

```python
INSTALLED_APPS = [
    ...
    "channels",
    "apps.voice",
]

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}
```

### 3. 数据库迁移

```bash
python manage.py makemigrations voice
python manage.py makemigrations chat
python manage.py migrate
```

### 4. 启动后端

```bash
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

## Nginx 配置

在 `/etc/nginx/sites-available/deeptutor` 中添加 WebSocket 路由：

```nginx
location /linchat/ws/ {
    proxy_pass http://linchat_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

```bash
sudo nginx -t && sudo nginx -s reload
```

## 前端设置

```bash
cd /home/dantsinghua/work/linchat/frontend

# 无需安装新依赖（使用浏览器原生 API）
npm run build
npm run start -- -p 3784
```

## 验证

### WebSocket 连接测试

```bash
# 使用 websocat 测试（安装: cargo install websocat）
websocat ws://localhost:8002/ws/voice/ -H "Cookie: <your-cookie>"
```

### REST API 测试

```bash
# 获取语音设置
curl -b cookie.txt http://localhost:8002/api/v1/voice/settings/

# 注册声纹
curl -b cookie.txt -F "audio=@test.wav" -F "name=测试用户" \
  http://localhost:8002/api/v1/voice/speakers/

# 注册设备
curl -b cookie.txt -X POST -H "Content-Type: application/json" \
  -d '{"name": "测试设备"}' \
  http://localhost:8002/api/v1/voice/devices/
```

## 关键文件参考

| 文件 | 用途 |
|------|------|
| `backend/apps/voice/consumers.py` | WebSocket 消费者（语音代理核心） |
| `backend/apps/voice/services/gateway_client.py` | llmgateway WebSocket 客户端 |
| `backend/apps/voice/services/response_decision_service.py` | 响应决策引擎 |
| `backend/core/asgi.py` | ASGI 路由（HTTP + WebSocket） |
| `frontend/src/hooks/useVoiceWebSocket.ts` | 前端 WebSocket 管理 |
| `frontend/src/hooks/usePCMAudioCapture.ts` | AudioWorklet PCM16 采集 |
| `frontend/src/components/voice/VoiceModePanel.tsx` | 语音控制面板 |
