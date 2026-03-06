# Quickstart: TTS 播报队列

## 验证场景

### 1. 单元测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/voice/test_tts_pipeline_manager.py -v
```

### 2. E2E 测试 — 安慰语音

1. 确保后端 + Gateway 服务运行中
2. Playwright 登录 LinChat（`/linchat-login`）
3. 切换到语音模式
4. 发送需要较长处理时间的请求（如"查一下家里有哪些设备"）
5. 验证：
   - 3s 后听到"正在思考，请稍后"
   - Agent 完成后 1s 静默 → 完整回复播报

### 3. E2E 测试 — 快速回复

1. 发送简单问题（如"你好"）
2. 验证：
   - 不播放安慰语音
   - 直接播报回复

### 4. 配置调整

```bash
# .env 可选覆盖
VOICE_TTS_COMFORT_DELAY=3.0      # 安慰延迟（秒）
VOICE_TTS_SEGMENT_GAP=1.0        # 段间静默（秒）
VOICE_TTS_COMFORT_TEXTS='["文本1","文本2","文本3"]'  # 自定义安慰文本
VOICE_TTS_ERROR_TEXT="自定义错误提示"
```
