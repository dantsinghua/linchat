# tests/voice 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖功能 |
|------|----------|
| `test_models.py` | SpeakerProfile / RegisteredDevice 模型 |
| `test_speaker_service.py` | 声纹注册/删除/识别（Gateway HTTP mock） |
| `test_asr_stream_client.py` | ASR WebSocket 客户端（连接/配置/音频转发/事件回调/断开） |
| `test_tts_stream_client.py` | TTS WebSocket 客户端（连接/text.delta/audio.done/超时） |
| `test_voice_pipeline.py` | VoicePipeline（Agent+TTS 编排/持久化/持续监听/barge-in/取消） |
| `test_voice_session.py` | 会话管理 / Redis 状态 / 音频缓存 / 频率限制 |
| `test_consumers.py` | WebSocket Consumer（认证/配置/音频转发/ASR 事件翻译） |
| `test_latency_benchmark.py` | 端到端语音延迟基准 |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/voice/ -v
```

## 核心 Mock

| Mock 目标 | 用途 |
|-----------|------|
| `websockets.connect` | Gateway ASR/TTS WebSocket 连接 |
| `apps.voice.services.speaker_service.httpx.AsyncClient` | 声纹 Gateway HTTP 请求 |
| `apps.graph.services.agent_service.AgentService` | Agent 推理 |
| `apps.graph.services.inference_service.InferenceService` | 推理任务管理 |
| `redis.asyncio.Redis` | 会话状态 / 音频缓存 |
| `channels.testing.WebsocketCommunicator` | Consumer 端到端测试 |
