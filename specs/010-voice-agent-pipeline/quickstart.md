# Quickstart: 语音模块迁移开发指南

**Date**: 2026-03-02 | **Feature**: 010-voice-agent-pipeline

## 前置条件

1. Gateway 已部署 007-voice-io 版本（提供 ASR WS + TTS 流式 WS 接口）
2. frpc-visitor 运行中（`127.0.0.1:8100` → Gateway）
3. LinChat 后端运行中（`0.0.0.0:8002`）

## 核心改动概览

| 组件 | 动作 | 说明 |
|------|------|------|
| `asr_stream_client.py` | **新增** | Gateway ASR WebSocket 客户端 |
| `tts_stream_client.py` | **新增** | TTS 流式 WebSocket 客户端 |
| `voice_pipeline.py` | **新增** | 语音管道编排（ASR→Agent→TTS） |
| `gateway_client.py` | **删除** | 旧 Gateway 全代理 WebSocket 客户端 |
| `voice_context_service.py` | **删除** | enriched 模式上下文构建 |
| `consumer_events.py` | **重写** | 事件翻译层 |
| `consumer_inference.py` | **重写** | 移除 enriched，接入 VoicePipeline |
| `consumer_session.py` | **修改** | 替换 GatewayClient → ASRStreamClient |
| `settings.py` | **修改** | 新增 VOICE_ASR/TTS 配置 |

## 开发步骤

### Step 1: 添加配置

```python
# core/settings.py
VOICE_ASR_WS_URL = "ws://127.0.0.1:8100/v1/audio/transcriptions/stream"
VOICE_TTS_URL = "ws://127.0.0.1:8100/v1/audio/speech/stream"  # WS 流式 TTS
VOICE_TTS_ENABLED = True
VOICE_TTS_VOICE = "zf_xiaobei"
VOICE_TTS_TIMEOUT = 30
VOICE_ASR_SPEECH_PAD_MS = 2000
VOICE_ASR_LANGUAGE = "auto"
```

### Step 2: 实现 ASRStreamClient

见 [plan.md](plan.md) "ASR Stream Client 设计" 节。

### Step 3: 实现 TTSStreamClient

见 [plan.md](plan.md) "TTSStreamClient 设计" 节和 [docs/tts-websocket-api.md](../../docs/tts-websocket-api.md)。

### Step 4: 实现 VoicePipeline

见 [plan.md](plan.md) "VoicePipeline 编排流程" 节。

### Step 5: 修改 Consumer Mixins

1. `consumer_session.py`: 替换 `GatewayClient` → `ASRStreamClient`
2. `consumer_events.py`: 重写事件映射（见 [contracts/gateway-asr-ws.md](contracts/gateway-asr-ws.md) 第 3 节）
3. `consumer_inference.py`: 移除 enriched，替换为 VoicePipeline 调用

### Step 6: 删除废弃代码

```bash
rm backend/apps/voice/services/gateway_client.py
rm backend/apps/voice/services/voice_context_service.py
```

### Step 7: 测试验证

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/voice/ -v
```

## 手动验证

1. 启动后端: `uvicorn core.asgi:application --host 0.0.0.0 --port 8002`
2. 公网访问: `https://www.greydan.xin/linchat`
3. 登录 → 点击语音模式按钮
4. 对麦克风说话 → 验证：
   - 看到转写文字
   - 看到 AI 回复文字
   - 听到 TTS 语音回复
5. 切换到文字模式 → 验证历史消息包含语音消息（`is_voice=True`）

## 关键参考

| 文档 | 用途 |
|------|------|
| [spec.md](spec.md) | 功能需求和验收标准 |
| [plan.md](plan.md) | 架构设计和代码结构 |
| [data-model.md](data-model.md) | 数据模型（无新增） |
| [contracts/gateway-asr-ws.md](contracts/gateway-asr-ws.md) | 接口契约和事件映射 |
| [research.md](research.md) | 技术决策和研究发现 |
| [../../docs/linchat-integration-guide.md](../../docs/linchat-integration-guide.md) | Gateway 接口权威文档 |
