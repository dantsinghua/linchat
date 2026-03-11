# 014-jarvis-ambient-voice 功能架构总结

> 本文档总结 014 特性的完整实现逻辑，供后续开发和维护参考。

---

## 一、功能概述

在现有语音交互基础上（009 语音交互 + 010 语音管道 + 013 安慰队列），新增 **ambient（环境监听）模式**，实现贾维斯式交互体验：

- **持续在场**：无唤醒词持续监听，ASR 连接长期保活（TTL=3600s）
- **耐心等待**：多段话语聚合后再处理，不逐条回复
- **智能决策**：8 层优先级判断是否需要回复
- **自然介入**：只在被需要时回复，默认保持沉默

核心解决"一问一答"的非自然对话问题。**仅 ambient 模式启用聚合+决策**，现有 `voice_chat` 和 `continuous_listen` 模式行为不变。

---

## 二、三大核心组件

### 2.1 UtteranceAggregator（话语聚合器）

**文件**：`backend/apps/voice/services/utterance_aggregator.py`

**职责**：缓冲多段 ASR 转录文本，静默超时后聚合为完整消息。

**数据结构**：

| 类 | 说明 |
|----|------|
| `AggregatorState` | 枚举：`IDLE → COLLECTING → AGGREGATED → IDLE` |
| `AggregatedMessage` | 聚合结果：`text`（拼接文本）、`utterance_count`、`first_ts`/`last_ts` |

**关键方法**：

| 方法 | 功能 |
|------|------|
| `add(text)` | 追加转录到缓冲，重置超时计时器；满缓冲自动 flush |
| `flush()` | 立即聚合（停止词触发时使用） |
| `reset()` | 清空缓冲，不触发回调（STOP 后用） |
| `destroy()` | 会话结束时清理 timer |
| `_on_timeout()` | Timer 回调，超时后自动聚合并触发 `on_aggregated` |

**聚合流程**：

```
用户说 "帮我开卧室灯" → add() → 缓冲区[1条], 启动3s计时器
  ↓ 1.2秒后
用户说 "还有空调也开" → add() → 缓冲区[2条], 重置3s计时器
  ↓ 3秒静默
超时触发 → 聚合为 "帮我开卧室灯 还有空调也开" → on_aggregated() 回调
```

**配置**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VOICE_AMBIENT_AGGREGATE_TIMEOUT` | 3.0s | 静默超时阈值 |
| `VOICE_AMBIENT_MAX_BUFFER_SIZE` | 10 | 最大缓冲话语数，满即自动 flush |

---

### 2.2 ResponseDecisionService（响应决策引擎）

**文件**：`backend/apps/voice/services/response_decision_service.py`

**职责**：聚合后的文本经过 8 层优先级判断，输出三路决策：`RESPOND` / `RECORD_ONLY` / `STOP`。

**8 层决策链（优先级从高到低）**：

| 优先级 | 条件 | 结果 | 说明 |
|--------|------|------|------|
| 1 | 紧急停止词（"停"、"取消"、"闭嘴"） | `STOP` | 预检阶段 |
| 2 | 精确唤醒词匹配（`w in text`） | `RESPOND` | 来自 VoiceSettings.wake_words |
| 3 | 模糊唤醒词匹配（编辑距离≤1 或拼音相似≥0.8） | `RESPOND` | pypinyin 库 |
| 4 | **LLM 意图分类**（httpx→DeepSeek，1s超时） | 视置信度 | 仅 ambient + `VOICE_DECISION_USE_LLM=True` |
| 5 | 活跃对话状态（Redis `voice:active_conv:{uid}` 30s内存在） | `RESPOND` | 最近有 Agent 活动 |
| 6 | 多说话人活跃（Redis `recent_speakers` SCARD≥2） | `RECORD_ONLY` | 可能是人与人对话 |
| 7 | 问句特征（含 ？ 或问词/语气词） | `RESPOND` | 中文问句启发式判断 |
| 8 | 默认兜底 | `RECORD_ONLY` | 宁可沉默不打扰 |

**核心理念**：宁可沉默不打扰。LLM 置信度低于阈值(0.7)时穿透到下层规则。

**配置**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VOICE_DECISION_USE_LLM` | False | LLM 意图分类开关（默认关闭） |
| `VOICE_DECISION_LLM_THRESHOLD` | 0.7 | 置信度阈值 |
| `VOICE_DECISION_LLM_TIMEOUT` | 1.0s | LLM 分类超时（超时穿透到规则 5-8） |

---

### 2.3 TTSRouter（跨设备 TTS 路由）

**文件**：`backend/apps/voice/services/tts_router.py`

**职责**：ESP 设备只上传音频（单向麦克风），AI 回复通过 Django Channels `group_send` 广播到用户的**浏览器连接**播放 TTS。

**关键方法**：

| 方法 | 功能 |
|------|------|
| `group_name(user_id)` | 返回 `voice_tts_{user_id}` 组名 |
| `send_binary(user_id, data)` | 广播音频帧到 Channels group |
| `get_on_audio_callback(user_id)` | 返回 `on_audio` 回调供 TTSPipelineManager 使用 |

**Consumer 端处理**（`consumers.py`）：

```python
async def tts_audio_frame(self, event):
    if self._is_device_connection:
        return  # ESP 设备跳过，不播放 TTS
    await self._send_binary(event["data"])
```

---

## 三、Consumer Mixin 架构

`VoiceConsumer` 由 3 个 Mixin 组合，各司其职：

### 3.1 SessionMixin（会话管理）

**文件**：`backend/apps/voice/consumer_session.py`

| 方法 | 功能 |
|------|------|
| `_handle_session_configure(mode="ambient")` | 初始化会话，ambient 模式创建 UtteranceAggregator |
| `_on_utterance_aggregated(msg)` | **聚合完成回调** — 串联决策→执行的核心枢纽 |
| `_handle_session_reconnect(data)` | ASR 断连重连（最多 3 次） |

**聚合完成回调流程**：

```python
async def _on_utterance_aggregated(self, aggregated_msg):
    # 1. 发送 aggregation.completed 事件到前端
    # 2. decision, reason = await ResponseDecisionService.decide(text, mode="ambient")
    # 3. 发送 decision.result 事件到前端
    # 4. 三路执行：
    #    - RESPOND:      await _start_voice_pipeline(text, mode="ambient")
    #    - RECORD_ONLY:  await VoicePipeline.record_only_ambient(user_id, text)
    #    - STOP:         (已在停止词预检中处理)
```

### 3.2 EventMixin（ASR 事件分发）

**文件**：`backend/apps/voice/consumer_events.py`

**转录完成的分支处理**：

```python
async def _on_transcription_completed(self, event):
    text = event.get("text", "").strip()

    if self._mode == "ambient":
        await self._handle_ambient_transcription(text, segment_id)
        return

    # voice_chat 模式：直接触发 Pipeline
    await self._start_voice_pipeline(segment_id, text)
```

**ambient 转录处理**：

```python
async def _handle_ambient_transcription(self, text, segment_id):
    # 1. 停止词预检（紧急中断）
    if ResponseDecisionService._check_emergency_stop(text):
        aggregator.reset()
        await VoicePipeline.cancel(user_id)
        发送 decision.result(STOP)
        return

    # 2. 正常路径：加入聚合器
    await aggregator.add(text)
    发送 aggregation.utterance_added 事件
```

### 3.3 InferenceMixin（推理管道启动）

**文件**：`backend/apps/voice/consumer_inference.py`

| 方法 | 功能 |
|------|------|
| `_start_voice_pipeline(segment_id, text)` | 后台异步启动 Pipeline task |
| `_idle_timeout_loop()` | ambient 模式**跳过 60s 空闲超时**，不断开连接 |

---

## 四、VoicePipeline 扩展

**文件**：`backend/apps/voice/services/voice_pipeline.py`

### 4.1 run_pipeline() ambient 分支

```python
@staticmethod
async def run_pipeline(user_id, text, segment_id, consumer, mode="voice_chat", speaker_id=None):
```

Pipeline 8 步执行：

1. **标记活跃对话**（仅 ambient）：`set_active_conversation(user_id)` → Redis 30s TTL
2. **Barge-in 互斥**：同用户同时只运行 1 个 pipeline（asyncio.Lock），新 segment 取消旧 pipeline
3. **频率限制检查**：LLM 60 次/分（Redis 计数）
4. **注册推理任务**：`InferenceService.register_task()`，复用 SSE 并发控制
5. **初始化 TTS**：
   - voice_chat → `on_audio = consumer._send_binary`（直接发前端）
   - **ambient → `on_audio = TTSRouter.get_on_audio_callback(user_id)`**（跨设备广播）
6. **流式 Agent 执行**：`AgentService.execute()` → 流式 chunk 分发
7. **等待 TTS 完成**：`tts_manager.wait_idle()` + `shutdown()`
8. **持久化音频附件**

### 4.2 record_only_ambient() 静默持久化

```python
@staticmethod
async def record_only_ambient(user_id, text, consumer):
    # 1. 创建 Message(role=user, is_voice=True, 无 request_id)
    # 2. 保存到 PostgreSQL
    # 3. 清理超限：保留最近 VOICE_AMBIENT_RECORD_ONLY_LIMIT(20) 条
```

---

## 五、完整数据流图

```
ESP 设备 → PCM 音频帧 → WebSocket
  ↓
VoiceConsumer.receive(binary)
  ↓ 转发到 Gateway ASR
ASRStreamClient → Gateway ASR WebSocket
  ↓ transcription.completed 事件
EventMixin._on_transcription_completed()
  ↓ ambient 分支
_handle_ambient_transcription()
  ├─ 停止词检查 → STOP: reset 聚合器 + cancel Pipeline
  └─ aggregator.add(text) → 3s 超时触发
     ↓
_on_utterance_aggregated(聚合结果)
  ├─ 发送 aggregation.completed 事件
  ├─ ResponseDecisionService.decide() → 8 层判断
  ├─ 发送 decision.result 事件
  └─ 三路执行：
     ├─ RESPOND → VoicePipeline.run_pipeline(mode="ambient")
     │   ├─ set_active_conversation (Redis 标记 30s)
     │   ├─ Barge-in 互斥锁
     │   ├─ 频率限制检查
     │   ├─ TTSPipelineManager(on_audio=TTSRouter 广播)
     │   ├─ AgentService.execute() 流式
     │   ├─ TTS 播报 → 浏览器接收音频
     │   └─ 持久化消息 + 音频附件
     ├─ RECORD_ONLY → VoicePipeline.record_only_ambient()
     │   ├─ 保存 Message(role=user, is_voice=True)
     │   └─ 清理超限（保留最近 20 条）
     └─ STOP → VoicePipeline.cancel()
```

---

## 六、WebSocket 协议（ambient 模式新增事件）

### 6.1 前端类型定义

**文件**：`frontend/src/types/voice.ts`

```typescript
type VoiceMode = 'voice_chat' | 'ambient';

// 014 新增事件类型
type VoiceWSEventType =
  | 'aggregation.utterance_added'  // 话语加入缓冲
  | 'aggregation.completed'        // 聚合完成
  | 'decision.result'              // 决策结果
  | 'tts.started'                  // TTS 开始
  | 'tts.completed'                // TTS 完成
  | ... // 既有事件

interface VoiceAggregationUtteranceAdded {
  text: string;             // 最新话语
  buffer_count: number;     // 缓冲话语总数
  timeout_remaining: number; // 剩余超时秒数
}

interface VoiceAggregationCompleted {
  aggregated_text: string;  // 聚合后完整文本
  utterance_count: number;  // 话语总数
  first_ts: number;         // 第一句时间戳
  last_ts: number;          // 最后一句时间戳
}

interface VoiceDecisionResult {
  decision: 'RESPOND' | 'RECORD_ONLY' | 'STOP';
  reason: string;           // 判定原因
  confidence?: number;      // LLM 置信度
}
```

### 6.2 消息时序（典型场景）

```
ESP → session.configure(mode="ambient")
  ← session.configured(features: { utterance_aggregation: true, cross_device_tts: true })

[用户说话]
  ← aggregation.utterance_added(text="帮我开卧室灯", buffer_count=1)
  ← aggregation.utterance_added(text="还有空调也开", buffer_count=2)
  ← aggregation.completed(aggregated_text="帮我开卧室灯 还有空调也开", utterance_count=2)
  ← decision.result(decision="RESPOND", reason="question_detected")
  ← response.start(...)
  ← response.delta(content="好的...")  ×N
  ← tts.started
  ← [binary: TTS 音频帧]  ×N（通过 TTSRouter 广播到浏览器）
  ← tts.completed
  ← response.end(...)
  ← message.saved(...)
```

---

## 七、Redis 键设计

| 键模式 | TTL | 用途 | 写入方 |
|--------|-----|------|--------|
| `voice:session:{uid}` | 3600s (ambient) / 120s (voice_chat) | 会话状态 JSON | voice_session_service |
| `voice:active_conv:{uid}` | 30s | 活跃对话标记（决策规则 5） | VoicePipeline |
| `voice:audio_chunks:{uid}:{seg}` | 300s | PCM 帧缓存 | voice_session_service |
| `voice:llm_rate:{uid}` | 60s | LLM 频率限制 | VoicePipeline |
| `voice:recent_speakers:{uid}` | 60s | 说话人集合（决策规则 6） | speaker_service |

---

## 八、配置汇总

| 配置项 | 默认值 | 建议范围 | 说明 |
|--------|--------|---------|------|
| `VOICE_AMBIENT_AGGREGATE_TIMEOUT` | 3.0s | 2.0-5.0s | 聚合静默超时 |
| `VOICE_AMBIENT_MAX_BUFFER_SIZE` | 10 | 5-20 | 最大缓冲话语数 |
| `VOICE_AMBIENT_SESSION_TTL` | 3600s | 1800-7200s | ambient 会话保活 |
| `VOICE_AMBIENT_RECORD_ONLY_LIMIT` | 20 | 10-50 | RECORD_ONLY 消息保留上限 |
| `VOICE_DECISION_USE_LLM` | False | — | LLM 意图分类开关 |
| `VOICE_DECISION_LLM_THRESHOLD` | 0.7 | 0.6-0.9 | LLM 置信度阈值 |
| `VOICE_DECISION_LLM_TIMEOUT` | 1.0s | 0.5-2.0s | LLM 分类超时 |
| `VOICE_TTS_COMFORT_DELAY` | 3.0s | 2.0-5.0s | 安慰语音延迟 |
| `VOICE_IDLE_TIMEOUT` | 60s | — | 空闲超时（ambient 模式跳过） |

---

## 九、与前序特性的关系

| 014 组件 | 复用/整合 | 来源 |
|----------|----------|------|
| ASR 转录 | 复用 ASRStreamClient | 009 语音交互 |
| VoicePipeline | 扩展管道，新增 `mode="ambient"` 分支 | 010 语音管道 |
| TTS 安慰队列 | 复用 TTSPipelineManager | 013 安慰队列 |
| Barge-in 打断 | 复用取消机制 | 013 安慰队列 |
| 唤醒词 | 复用 VoiceSettings.wake_words | 009 语音交互 |
| Channels group | 扩展支持跨设备 TTS 广播 | 009 WebSocket 基础 |

---

## 十、关键文件索引

| 文件 | 职责 |
|------|------|
| `backend/apps/voice/services/utterance_aggregator.py` | 话语聚合器 |
| `backend/apps/voice/services/response_decision_service.py` | 8 层响应决策引擎 |
| `backend/apps/voice/services/tts_router.py` | 跨设备 TTS 路由 |
| `backend/apps/voice/services/voice_pipeline.py` | 语音推理管道（ambient 扩展） |
| `backend/apps/voice/consumers.py` | WebSocket Consumer 主类 |
| `backend/apps/voice/consumer_session.py` | SessionMixin — 聚合器初始化 + 回调 |
| `backend/apps/voice/consumer_events.py` | EventMixin — ASR 事件分发 + ambient 转录处理 |
| `backend/apps/voice/consumer_inference.py` | InferenceMixin — Pipeline 启动 + 空闲超时 |
| `backend/core/settings.py` | 全部 ambient 配置项 |
| `frontend/src/types/voice.ts` | 前端类型定义（ambient 模式 + 新事件） |

---

## 十一、故障排查

| 现象 | 可能原因 | 排查方法 |
|------|---------|---------|
| 聚合器不触发 | `_mode != "ambient"` 或 timer 被 cancel | 检查 session.configure 的 mode 参数 |
| 每句话都回复 | 决策引擎短路（唤醒词匹配/活跃对话标记） | 查看 decision.result 事件中的 reason |
| 该回复时不回复 | 默认兜底 RECORD_ONLY | 考虑开启 `VOICE_DECISION_USE_LLM=True` |
| TTS 未到浏览器 | TTSRouter group 不匹配或 ESP 设备标记错误 | 检查 Channels group_send 的 user_id |
| RECORD_ONLY 消息丢失 | 清理任务异常或 LIMIT 过小 | 查看 _cleanup_record_only_messages 日志 |
| ambient 会话频繁断开 | `VOICE_AMBIENT_SESSION_TTL` 过短或 ASR 重连失败 | 调整 TTL，检查 ASR 重连日志（最多 3 次） |
| LLM 决策超时 | DeepSeek API 延迟 | 检查 `VOICE_DECISION_LLM_TIMEOUT` 配置 |
