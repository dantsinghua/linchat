# Quickstart: Ambient 模式说话人识别

**Branch**: `017-ambient-speaker-id`

## 前置条件

1. LinChat 后端运行中（`./scripts/services.sh status` 确认）
2. Gateway 声纹服务可用（`POST /v1/voice/speakers` 可达）
3. 至少 1 个家庭成员已注册声纹（通过设置页面或 API）
4. reSpeaker 设备连接正常（016 特性）

## 开启功能

```bash
# 1. 在 backend/.env 中添加
VOICE_SPEAKER_IDENTIFICATION_ENABLED=True

# 2. 重启后端
cd /home/dantsinghua/work/linchat
./scripts/services.sh restart
```

## 验证步骤

### 1. 验证说话人识别

```bash
# 确认声纹已注册
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py shell -c "
from apps.voice.models import SpeakerProfile
for sp in SpeakerProfile.objects.all():
    print(f'{sp.name} (user_id={sp.user_id}, gateway_id={sp.gateway_speaker_id})')
"
```

### 2. 测试 ambient 模式

1. 打开浏览器 → LinChat → 开启 ambient 模式
2. 对着 reSpeaker 说一句话
3. 检查浏览器 WebSocket 消息中是否有 `speaker.identified` 事件
4. 检查消息气泡旁是否显示说话人头像/名称

### 3. 验证 TTS 回声过滤

1. 在 ambient 模式下说出唤醒词触发回复
2. 等待 TTS 播放
3. 检查 TTS 播放期间的 ASR 转录是否被自动丢弃（日志中应有 `tts_echo_detected`）

### 4. 验证未知说话人

1. 让未注册声纹的人对着 reSpeaker 说话
2. 检查前端显示数字标签（如"用户01"）
3. 同一人再说一句，确认标签保持一致

## 关闭功能（回滚）

```bash
# backend/.env
VOICE_SPEAKER_IDENTIFICATION_ENABLED=False

# 重启
./scripts/services.sh restart
```

关闭后 ambient 模式恢复原有行为：所有语音归属 WebSocket 连接用户。

## 运行测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 说话人识别测试
pytest tests/voice/test_speaker_identification.py -v

# TTS 回声过滤测试
pytest tests/voice/test_tts_echo_detection.py -v

# 未知说话人标签测试
pytest tests/voice/test_unknown_speaker_labeling.py -v

# 全量语音测试
pytest tests/voice/ -v
```

## 调试

```bash
# 查看后端日志中的识别结果
grep "speaker_identify" /home/dantsinghua/work/linchat/backend/logs/linchat.log

# 查看 TTS 回声过滤
grep "tts_echo" /home/dantsinghua/work/linchat/backend/logs/linchat.log

# 检查 Redis 临时标签
redis-cli -a redis_linchat_123 HGETALL voice:unknown_speakers
redis-cli -a redis_linchat_123 GET voice:unknown_counter
```
