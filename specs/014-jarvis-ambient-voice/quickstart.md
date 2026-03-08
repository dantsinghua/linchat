# Quickstart: 014-jarvis-ambient-voice

## 前置条件

1. LinChat 后端运行中（uvicorn ASGI 模式）
2. Redis 运行中（DB 0: 缓存, DB 3: Channels）
3. Gateway ASR 服务可达（localhost:8100）
4. 已完成 009/010/013 特性

## 快速验证步骤

### 1. 配置参数（settings.py 新增）

```python
# Ambient 模式聚合配置
VOICE_AMBIENT_AGGREGATE_TIMEOUT = 3.0      # 聚合静默超时（秒）
VOICE_AMBIENT_MAX_BUFFER_SIZE = 10         # 单次聚合最大话语段数
VOICE_AMBIENT_SESSION_TTL = 3600           # ambient 会话 TTL（秒）
VOICE_AMBIENT_RECORD_ONLY_LIMIT = 20       # RECORD_ONLY 消息保留上限

# LLM 意图分类（默认关闭，规则引擎优先）
VOICE_DECISION_USE_LLM = False
VOICE_DECISION_LLM_THRESHOLD = 0.7
VOICE_DECISION_LLM_TIMEOUT = 1.0
```

### 2. 启动后端

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
PYTHONUNBUFFERED=1 uvicorn core.asgi:application --host 0.0.0.0 --port 8002
```

### 3. WebSocket 连接测试（浏览器端模拟）

使用 websocat 或浏览器 DevTools 连接：

```bash
# 浏览器端连接（需要有效 session cookie）
websocat ws://localhost:8002/ws/voice/ -H "Cookie: sessionid=<your-session-id>"
```

发送配置消息：

```json
{"type": "session.configure", "mode": "ambient"}
```

期望收到：

```json
{
  "type": "session.configured",
  "mode": "ambient",
  "features": {
    "utterance_aggregation": true,
    "llm_decision": false,
    "cross_device_tts": true
  }
}
```

### 4. ESP 设备连接测试

```bash
# ESP 设备连接（device token 认证）
websocat ws://localhost:8002/ws/voice/?token=<device-api-token>
```

发送配置后，发送 PCM 音频帧（binary），观察：

1. `transcription.completed` 事件 — ASR 识别完成
2. `aggregation.utterance_added` 事件 — 话语加入缓冲区
3. 静默 3 秒后 `aggregation.completed` 事件 — 聚合触发
4. `decision.result` 事件 — 决策结果
5. 如果 RESPOND：`response.delta` + `response.done` — Agent 回复

### 5. 跨设备 TTS 验证

1. ESP 设备连接 ambient 模式（input only）
2. 浏览器连接同一 user_id 的 voice WebSocket
3. ESP 上传触发 RESPOND 决策的音频
4. 浏览器端收到 `tts.started` + TTS 音频帧 + `tts.completed`

## 关键文件

| 文件 | 用途 |
|------|------|
| `backend/apps/voice/services/utterance_aggregator.py` | 话语聚合缓冲区 |
| `backend/apps/voice/services/tts_router.py` | 跨设备 TTS 路由 |
| `backend/apps/voice/services/response_decision_service.py` | 增强响应决策（+LLM 分类） |
| `backend/apps/voice/services/voice_pipeline.py` | ambient 模式管道分支 |
| `backend/apps/voice/consumer_events.py` | ambient 转录事件路由到聚合器 |
| `backend/apps/voice/consumer_session.py` | ambient 会话管理 |
| `backend/apps/voice/consumer_inference.py` | 禁用 ambient 空闲超时 |

## 测试命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行所有 ambient 相关测试
pytest tests/voice/test_utterance_aggregator.py -v
pytest tests/voice/test_tts_router.py -v
pytest tests/voice/test_response_decision_llm.py -v
pytest tests/voice/test_voice_pipeline.py -v -k ambient

# 运行全量语音测试
pytest tests/voice/ -v
```

## 故障排查

| 症状 | 排查方向 |
|------|----------|
| 聚合不触发 | 检查 `VOICE_AMBIENT_AGGREGATE_TIMEOUT` 值；确认 mode=ambient |
| LLM 分类超时 | 检查 `VOICE_DECISION_USE_LLM` 是否开启；检查 DeepSeek API 可达性 |
| TTS 不播放 | 确认浏览器连接已建立；检查 `voice_tts_{uid}` 分组成员 |
| ASR 断连 | 检查 Gateway 服务状态；查看重连日志 |
| ESP 认证失败 | 验证 device token 有效性；检查 RegisteredDevice 记录 |
