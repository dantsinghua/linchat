# WebSocket Event Contracts: Speaker Identification

**Date**: 2026-04-15 | **Branch**: `017-ambient-speaker-id`

## 新增事件

### `speaker.identified`

**方向**: Server → Client
**触发时机**: 每段语音转录完成后，说话人识别结果返回时

```json
{
  "type": "speaker.identified",
  "data": {
    "segment_id": "seg_abc123",
    "speaker_user_id": 5,
    "speaker_label": "安琳",
    "confidence": 0.87,
    "is_identified": true
  }
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| segment_id | string | 是 | 语音段 ID，关联 ASR 转录结果 |
| speaker_user_id | int \| null | 是 | 已识别: user_id；未识别: null |
| speaker_label | string | 是 | 已识别: 用户名；未识别: "unknown_01" |
| confidence | float | 是 | 识别置信度 (0.0-1.0)；未识别时为 0.0 |
| is_identified | boolean | 是 | 是否成功识别为已注册用户 |

**已识别用户示例**:

```json
{
  "type": "speaker.identified",
  "data": {
    "segment_id": "seg_001",
    "speaker_user_id": 5,
    "speaker_label": "安琳",
    "confidence": 0.87,
    "is_identified": true
  }
}
```

**未识别用户示例**:

```json
{
  "type": "speaker.identified",
  "data": {
    "segment_id": "seg_002",
    "speaker_user_id": null,
    "speaker_label": "unknown_01",
    "confidence": 0.0,
    "is_identified": false
  }
}
```

**功能关闭时**: 不发送此事件。

## 已有事件（无改动）

以下事件已在 `useVoiceWebSocket.ts` EVENT_HANDLER_MAP 中定义，本特性不改动：

- `transcription.partial` — ASR 部分结果
- `transcription.completed` — ASR 完成
- `decision.result` — 响应决策结果
- `agent.response` — Agent 回复
- `tts.audio` — TTS 音频帧

## 决策结果扩展

`decision.result` 事件新增 `DISCARD` 类型：

```json
{
  "type": "decision.result",
  "data": {
    "decision": "DISCARD",
    "reason": "tts_echo_detected",
    "text": "帮我查一下明天天气"
  }
}
```

`DISCARD` 决策表示识别为 TTS 回声，已丢弃，不触发 Agent 推理。
