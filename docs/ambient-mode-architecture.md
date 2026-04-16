# Ambient 模式全链路技术架构

> reSpeaker WiFi 环境监听模式的完整数据流、服务依赖与核心逻辑剖析。

---

## 一、整体架构概览

```
reSpeaker 硬件 (XVF3800)
  | WiFi (PCM 16kHz/16bit/mono)
  v
WebSocket (ws/voice/) ---- 设备 Token 认证
  |
  v
VoiceConsumer (3 Mixin 架构)
  |
  |-- SessionMixin ---- ASR 连接管理 + 聚合器初始化 + 设备独占
  |-- EventMixin ------ ASR 事件分发 + ambient 分支路由
  +-- InferenceMixin -- Pipeline 启动 + pending 缓冲
  |
  v  PCM 音频帧转发
  |
ASRStreamClient (BaseWSClient)
  | WebSocket
  v
Gateway ASR 服务 (外部)
  | 返回 VAD 事件 + 转录结果
  v
UtteranceAggregator (3 秒聚合)
  |
  v
ResponseDecisionService (8 级决策链)
  |
  |-- RESPOND -----> VoicePipeline -> AgentService -> TTSPipelineManager -> TTSRouter -> 浏览器/HA 音箱
  |-- RECORD_ONLY -> voice_persist_service.record_only_ambient() (保存到 DB)
  +-- STOP --------> cancel pipeline + reset aggregator
```

---

## 二、连接建立阶段

### 2.1 WebSocket 握手

**源文件**: `apps/voice/consumers.py:24-71`

reSpeaker 设备通过 `ws://host/ws/voice/?token=xxx` 连接。Consumer 区分两种连接来源:

| 来源 | 认证方式 | 标记 |
|------|----------|------|
| 设备 | URL 参数 `token` -> `device_service.authenticate_by_token()` SM4 解密匹配 | `_is_device_connection = True` |
| 浏览器 | Cookie 中间件已解析 `user_id` | `_is_device_connection = False` |

连接建立后初始化状态:

```python
self._mode = "ambient"           # 默认 ambient 模式
self._aggregator = None          # 聚合器（稍后 configure 时创建）
self._pending_text = None        # pipeline 忙时的缓冲文本
self._is_speaking = False        # VAD 说话状态
self._pipeline_task = None       # 当前 pipeline asyncio.Task
```

然后加入 Channels 分组 `voice_tts_{user_id}`，后续 TTS 音频帧通过 group_send 广播。

### 2.2 会话配置

**源文件**: `apps/voice/consumer_session.py:51-85`

客户端发送 `session.configure` 消息后:

1. **创建 Redis 会话** -> `voice:session:{uid}`（TTL 3600 秒）
2. **连接 Gateway ASR** -> `ASRStreamClient` 建立 WebSocket
3. **配置 ASR** -> 发送 `configure` 消息（auto_commit=True, speech_pad_ms, language）
4. **设备独占检查**（仅 ambient 模式）
5. **创建 UtteranceAggregator** -> 回调指向 `_on_utterance_aggregated()`
6. **返回 `session.configured`** -> 含 features（utterance_aggregation, llm_decision, cross_device_tts）

### 2.3 设备独占机制

**源文件**: `apps/voice/consumer_session.py:87-129`

ambient 模式同一用户只允许一个活跃连接，**设备优先于浏览器**:

| 场景 | 行为 |
|------|------|
| 已有设备连接 + 新来浏览器 | 拒绝浏览器，返回 `DEVICE_EXCLUSIVE` 错误 |
| 已有浏览器连接 + 新来设备 | 通过 Channels `force_disconnect` 踢掉旧浏览器 |
| 同类连接 | 踢掉旧连接，新连接接管 |

Redis 键 `voice:ambient_conn:{uid}` 存储当前连接的 `channel_name` 和 `is_device` 标记。

---

## 三、音频采集与 ASR 阶段

### 3.1 音频帧转发

**源文件**: `apps/voice/consumer_session.py:212-218`

reSpeaker 发送二进制 PCM 帧 -> Consumer `receive(bytes_data)` -> `_handle_audio_frame()`:

```python
async def _handle_audio_frame(self, pcm_data: bytes) -> None:
    # 1. 转发给 Gateway ASR
    await self._asr_client.send_audio(pcm_data)
    # 2. 缓存到 Redis（用于后续持久化 WAV）
    await voice_session_service.cache_audio_chunk(user_id, segment_id, pcm_data)
    # 3. 刷新会话活跃时间
    await voice_session_service.refresh_session(user_id)
```

### 3.2 ASR 服务通信

**源文件**: `apps/voice/services/asr_stream_client.py` + `apps/voice/services/ws_client_base.py`

**使用的服务**: LLM Gateway 的 ASR WebSocket 端点

**连接方式**: `BaseWSClient._connect_ws()` -> `websockets.connect(url, ping_interval=30, ping_timeout=60)` — 长连接，30 秒心跳保活

**数据流转**:

| 方向 | 内容 | 方式 |
|------|------|------|
| 上行 | PCM 二进制帧 | `_send_bytes_msg()` -> Gateway |
| 下行 | JSON 事件 | `_receive_loop()` -> `_handle_message()` -> 解析 JSON -> 回调 `_on_event()` -> Consumer `_handle_asr_event()` |

**ASR 内置 VAD**: Gateway ASR 服务内部集成了 VAD（Voice Activity Detection），不是单独的服务。ASR 返回的事件类型:

| 事件 | 含义 |
|------|------|
| `vad.speech_start` | 检测到人声开始 |
| `vad.speech_end` | 检测到人声结束 |
| `transcription.completed` | 一段语音的转录完成 |
| `transcription.failed` | 转录失败 |
| `error` | ASR 错误 |

**关键配置**:

| 参数 | 说明 |
|------|------|
| `speech_pad_ms` | VAD 判定语音结束后额外等待的毫秒数（静默尾部保护） |
| `auto_commit=True` | VAD 自动触发转录提交 |
| `VOICE_MAX_SEGMENT_DURATION` | 语音段超时保护，到期后强制 `send_commit()`，防止无限长语音段 |

### 3.3 VAD 事件处理

**源文件**: `apps/voice/consumer_events.py:25-40`

```
vad.speech_start -> self._is_speaking = True
                 -> 生成 segment_id（UUID 前 8 位）
                 -> set_active_conversation（Redis 标记活跃对话）
                 -> 启动语音段超时定时器
                 -> 推送前端 vad.speech_start 事件

vad.speech_end   -> self._is_speaking = False
                 -> 取消语音段超时定时器
                 -> 推送前端 vad.speech_end 事件
```

---

## 四、转录完成后的 Ambient 分支

**源文件**: `apps/voice/consumer_events.py:42-89`

`transcription.completed` 事件到达后，ambient 模式走独立分支:

```python
async def _on_transcription_completed(self, event):
    text = event.get("text", "").strip()
    # ... 推送 transcription.complete 给前端 ...
    if self._mode == "ambient":
        await self._handle_ambient_transcription(text, segment_id)
        return
    # voice_chat 模式直接进 pipeline
    await self._start_voice_pipeline(segment_id, text)
```

### 4.1 停止词预检（零延迟）

```python
if ResponseDecisionService._check_emergency_stop(text):
    # 紧急停止词: 停/取消/闭嘴/停止/别说了
    aggregator.reset()               # 清空缓冲区
    await VoicePipeline.cancel(user_id)  # 取消正在运行的 pipeline
    # 推送 decision.result: STOP
    return
```

这一步**不经过聚合器**，直接在流式 ASR 文本上检测，实现零延迟打断。

### 4.2 话语聚合

**源文件**: `apps/voice/services/utterance_aggregator.py`

非停止词文本进入聚合器:

```python
await self._legacy_aggregate(text, segment_id)
  -> aggregator.add(text)
  -> 推送 aggregation.utterance_added 事件给前端
```

**UtteranceAggregator 状态机**:

```
IDLE --add()--> COLLECTING --超时/满缓冲--> AGGREGATED --回调完成--> IDLE
                    |                          |
                    | 每次 add() 重置定时器      | _do_aggregate()
                    | buffer >= 10 则立即触发    | 拼接所有文本 -> 回调
                    +---------------------------+
```

**核心逻辑**:

| 触发条件 | 行为 |
|----------|------|
| `add(text)` | 追加到 `_utterances` 列表 + 重置 3 秒定时器 |
| 满缓冲（>=10 段） | 立即 `_do_aggregate()` |
| 3 秒静默超时 | 定时器到期 -> `_do_aggregate()` |
| `_do_aggregate()` | `" ".join(所有文本)` -> 构造 `AggregatedMessage` -> 触发回调 |

**为什么要聚合**: ambient 模式是持续监听，说话人可能说一句话被 VAD 切成多个语音段。3 秒聚合窗口将这些碎片拼成一句完整的话，再做决策。

---

## 五、响应决策

**源文件**: `apps/voice/services/response_decision_service.py`

聚合完成后进入 `_on_utterance_aggregated()`（`consumer_session.py:150-178`），调用决策服务:

```python
decision, reason = await response_decision_service.decide(
    aggregated_msg.text, speaker_id=None, user_id=target_uid,
    mode="ambient", speaker_identified=is_identified)
```

### 8 级决策链（按优先级从高到低）

| 级别 | 条件 | 结果 | 服务/数据源 |
|------|------|------|------------|
| 1 | 紧急停止词（停/取消/闭嘴/停止/别说了） | **STOP** | 纯文本匹配 |
| 2 | 唤醒词精确匹配（`w in text`） | **RESPOND** | PostgreSQL `VoiceSettings.wake_words` |
| 3 | 唤醒词模糊匹配（编辑距离<=1 或拼音相似度>=0.8） | **RESPOND** | pypinyin + 编辑距离算法 |
| 4 | LLM 意图分类（仅 `VOICE_DECISION_USE_LLM=True`） | **视置信度** | httpx -> LLM Gateway (kimi-k2.5) |
| 5 | 活跃对话状态（Redis 键存在） | **RESPOND** | Redis `voice:active_conv:{uid}` |
| 6 | 多 speaker（>=2 人在说话） | **RECORD_ONLY** | Redis `voice:recent_speakers:{uid}` |
| 7 | 问句特征（?/什么/怎么/吗/呢...） | **RESPOND** | 纯文本特征匹配 |
| 8 | 默认 | **RECORD_ONLY** | -- |

### 5.1 LLM 意图分类详解（第 4 级）

**使用的服务**: 通过 httpx 直接调用 LLM API（非 LangGraph Agent，轻量调用）

**数据流转**:

1. 从 `ModelConfig` 表获取活跃 tool 模型配置（kimi-k2.5）
2. **上下文增强**（`_fetch_intent_context()`）:
   - 从 `message_repo` 取最近 5 条对话（role + content[:200]）
   - 从 `MemoryService` 召回 3 条相关用户记忆
3. 渲染 Jinja2 模板 `voice_intent_classify.j2`（包含转录文本 + 对话上下文 + 记忆）
4. httpx POST -> LLM API（JSON mode, temperature=0.1, max_tokens=100）
5. 返回 `{decision, reason, confidence}`
6. 置信度 >= `VOICE_DECISION_LLM_THRESHOLD` -> 采用 LLM 决策
7. 低于阈值 -> 穿透到下一级规则
8. **超时安全降级**: LLM 超时 -> 返回 `RECORD_ONLY`（confidence=1.0），不穿透规则链

---

## 六、决策执行

### 6.1 RESPOND -> Voice Pipeline

**源文件**: `apps/voice/services/voice_pipeline.py`

```python
if decision.value == "RESPOND":
    if self._is_pipeline_busy():
        # Pipeline 正在忙 -> 缓冲到 _pending_text
        self._pending_text += " " + aggregated_msg.text
    else:
        await self._start_voice_pipeline(segment_id, text)
```

**Pipeline 内部流程**（`_run_inner()`）:

```
1. 频率限制检查 -> voice:llm_rate:{uid}（60 次/分）
2. InferenceService.register_task() -> Redis 注册推理任务
3. 创建 TTSPipelineManager
   +-- ambient 模式: on_audio = TTSRouter.get_on_audio_callback()
       （通过 Channels group_send 广播，而非直连 Consumer）
4. 推送 response.start -> 前端
5. AgentService.execute() 流式迭代
   +-- 每个 content chunk -> 推送 response.delta + 累积 full_response
   +-- 安慰语音计时器在后台运行
6. Agent 完成 -> stop_comfort_timer() -> enqueue(full_response, "response")
7. TTS 播报（见下文 TTS 阶段）
8. InferenceService.complete_task() -> 清理 Redis 推理注册
9. TTSRouter.send_control("tts.completed")
10. 尝试 HA 音箱播报（可选）
11. 推送 response.end -> 前端
12. 持久化音频附件（PCM -> WAV -> MinIO -> Message.is_voice + MediaAttachment）
```

**Barge-in 打断机制**（`run_pipeline()` 入口）:

```python
lock = _get_lock(conn_uid)       # 每用户一把 asyncio.Lock
if lock.locked():                # 有 pipeline 在跑
    await VoicePipeline.cancel()  # 取消旧 pipeline（推理+TTS）
    await asyncio.wait_for(lock.acquire(), timeout=2.0)  # 等锁释放
async with lock:                 # 独占执行新 pipeline
    await _run_inner(...)
```

### 6.2 RECORD_ONLY -> 静默记录

```python
elif decision.value == "RECORD_ONLY":
    await voice_persist_service.record_only_ambient(user_id=target_uid, text=text)
```

保存一条 `role=user` 的消息到 DB，上限 20 条自动清理最旧的。这些是"听到了但不回复"的环境对话记录。

### 6.3 STOP

停止词在聚合器之前就拦截了（步骤 4.1），不会走到决策执行阶段。

---

## 七、TTS 阶段

### 7.1 TTSPipelineManager

**源文件**: `apps/voice/services/tts_pipeline_manager.py`

异步队列管理器，编排 3 种播报类型:

```
安慰语音（comfort） -> Agent 回复（response） -> 错误播报（error）
```

**安慰语音 3 级递进**:

| 步骤 | 说明 |
|------|------|
| Pipeline 启动 | 开始计时（`VOICE_TTS_COMFORT_DELAY` 秒） |
| 超时 | 入队第 1 条安慰文本 |
| 播完 | 自动启动下一级计时，最多 3 条（`VOICE_TTS_COMFORT_TEXTS`） |
| Agent 完成 | `stop_comfort_timer()` -> 清空队列中未播的 comfort 项 |

**Worker 循环**:

```python
while True:
    item = await self._queue.get()
    await self._ensure_gap()         # 段间间隔
    await self._play_text(item.text) # 创建 TTSStreamClient -> Gateway TTS
    if item.item_type == "comfort":
        self.start_comfort_timer()   # 播完安慰后启动下一级
```

**QueueItem 类型**: `comfort`（安慰） | `response`（Agent 回复） | `error`（错误播报） | `sentinel`（关闭信号）

### 7.2 TTS 服务通信

**源文件**: `apps/voice/services/tts_stream_client.py` + `apps/voice/services/ws_client_base.py`

**使用的服务**: LLM Gateway 的 TTS WebSocket 端点

**每次 `_play_text()` 创建一个新的 TTSStreamClient**:

| 步骤 | 方法 | 说明 |
|------|------|------|
| 1 | `connect()` | `websockets.connect(TTS_WS_URL)` -> 获取 `session_id` + `sample_rate` |
| 2 | `configure(voice, speed)` | 配置声音 |
| 3 | `send_text_delta(text)` | 发送完整文本 |
| 4 | `send_text_done()` | 通知 Gateway 文本输入完毕，flush 缓冲 |
| 5 | Gateway 返回 binary 音频帧 | `_handle_message()` -> 回调 `on_audio(bytes)` |
| 6 | Gateway 返回 `audio.done` | 标记完成 |
| 7 | `disconnect()` | 关闭连接 |

### 7.3 TTSRouter — 跨设备广播

**源文件**: `apps/voice/services/tts_router.py`

**为什么需要 Router**: ambient 模式下，reSpeaker 设备发送音频 -> 服务端处理 -> TTS 音频需要发到**浏览器**播放。设备和浏览器是不同的 WebSocket 连接。

**实现方式**: Django Channels `group_send`

```
TTSPipelineManager.on_audio(bytes)
  -> TTSRouter.send_binary(user_id, bytes)
    -> channel_layer.group_send("voice_tts_{uid}", {type: "tts_audio_frame", data: bytes})
      -> 所有该用户的 Consumer 收到
        -> tts_audio_frame(event) -> 仅非设备连接 -> _send_binary() -> 浏览器
```

**Consumer 端过滤**（`consumers.py:156-162`）:

```python
async def tts_audio_frame(self, event):
    if not self._is_device_connection:  # 设备不回放自己的 TTS
        await self._send_binary(event["data"])
```

### 7.4 HA 音箱 TTS（可选，016 新增）

Agent 回复完成后，如果用户配置了 `tts_output_device=ha_speaker`:

```
_try_ha_speaker_tts()
  |-- 优先: xiaomi_miot.intelligent_speaker（HTTP POST 直传文本，零延迟）
  |-- 降级: Gateway TTS -> PCM->WAV -> MinIO 上传 -> media_player.play_media
  +-- 不可达: send_warning() -> 浏览器显示降级通知（浏览器 TTS 已正常播放）
```

---

## 八、Pipeline 完成后的 Pending 处理

**源文件**: `apps/voice/consumer_inference.py:54-78`

Pipeline 执行完毕后检查是否有缓冲的文本（`_pending_text`）:

```python
async def _on_pipeline_done(self):
    pending = self._pending_text
    if not pending:
        return
    self._pending_text = None

    if is_speaking or aggregator.state == "COLLECTING":
        # 用户还在说话或聚合器还在收集 -> 喂回聚合器
        await aggregator.add(pending)
    else:
        # 用户已停止说话 -> 直接启动新 pipeline
        await self._start_voice_pipeline("pending", pending)
```

---

## 九、各服务职责汇总

| 阶段 | 服务 | 协议/方式 | 部署位置 |
|------|------|-----------|----------|
| VAD | Gateway ASR 内置 | WebSocket（嵌入 ASR 流） | 远端 Gateway |
| ASR | Gateway ASR | WebSocket 长连接（30s 心跳） | 远端 Gateway |
| 话语聚合 | UtteranceAggregator | 进程内 asyncio | 本地后端 |
| 停止词检测 | ResponseDecisionService | 纯文本匹配 | 本地后端 |
| 唤醒词检测 | ResponseDecisionService | PostgreSQL + pypinyin | 本地后端 |
| LLM 意图分类 | kimi-k2.5（DashScope） | httpx HTTP POST | 远端 LLM API |
| 意图上下文增强 | message_repo + MemoryService | PostgreSQL + pgvector | 本地后端 |
| Agent 推理 | AgentService (LangGraph) | 进程内 astream_events | 本地后端 + 远端 LLM |
| TTS | Gateway TTS | WebSocket 短连接（每次新建） | 远端 Gateway |
| TTS 广播 | TTSRouter (Channels) | Redis DB3 group_send | 本地后端 |
| HA 音箱播报 | Home Assistant API | httpx HTTP POST | 局域网 HA |
| 音频持久化 | voice_persist_service | PCM->WAV + MinIO | 本地后端 |
| 会话管理 | voice_session_service | Redis DB0 | 本地后端 |
| 推理任务注册 | InferenceService | Redis DB0 | 本地后端 |

---

## 十、完整时序（一句话从说到回）

```
[0ms]     reSpeaker 采集 PCM -> WebSocket 发送
[持续]    Consumer 转发 PCM -> Gateway ASR + 缓存到 Redis
[~500ms]  Gateway VAD 检测到语音开始 -> vad.speech_start
[持续]    继续转发 PCM
[说完]    Gateway VAD 检测到静默 -> vad.speech_end
[~200ms]  Gateway ASR 输出 transcription.completed -> "今天天气怎么样"
[0ms]     停止词预检 -> 不是停止词
[0ms]     aggregator.add("今天天气怎么样") -> 状态变 COLLECTING
[3000ms]  3 秒内无新语音 -> 聚合超时 -> _do_aggregate()
[0ms]     ResponseDecisionService.decide()
          -> 唤醒词检测 -> 未命中
          -> LLM 意图分类 -> "对话意图, confidence=0.92" -> RESPOND
[0ms]     VoicePipeline.run_pipeline() -> 获取锁
[~50ms]   频率限制 + 推理注册 + TTS 管理器启动
[~1000ms] AgentService.execute() -> LLM 首 token
[持续]    流式输出 -> response.delta 推送前端
[~3000ms] 安慰计时器超时 -> 入队安慰语音 -> Gateway TTS -> 浏览器播放
[~5000ms] Agent 完成 -> 停止安慰 -> 入队完整回复
[~1000ms] Gateway TTS 合成 -> 音频帧 -> TTSRouter -> 浏览器播放
[完成]    推理注册清理 + tts.completed + 音频持久化
[可选]    HA 音箱播报（xiaomi_miot 直传文本）
```

---

## 十一、并发场景: 第二句话打断第一句

当 Agent 正在回复第一句话时，用户说了第二句:

| | 第一句 | 第二句 |
|---|---|---|
| **结果** | 被取消 | 正常执行 |
| **推理** | `signal_stop()` 中断 LLM | 新的完整 Agent 执行 |
| **TTS** | TTSPipelineManager 立即 cancel，音频停播 | 全新 TTS 播放 |
| **锁** | `_run_inner()` 完成后释放 asyncio.Lock | 等待锁释放（最多 2 秒），然后独占执行 |

---

## 十二、关键源文件索引

| 文件 | 行数 | 核心职责 |
|------|------|----------|
| `apps/voice/consumers.py` | 163 | Consumer 骨架 + Mixin 组装 + 设备认证 + TTS 回放过滤 |
| `apps/voice/consumer_events.py` | 131 | ASR 事件分发 + ambient 停止词预检 + 聚合器路由 |
| `apps/voice/consumer_inference.py` | 99 | Pipeline 启动 + pending 缓冲 + 空闲超时 |
| `apps/voice/consumer_session.py` | 249 | ASR 连接管理 + 聚合器初始化 + 设备独占 + 聚合回调 |
| `apps/voice/services/ws_client_base.py` | 86 | WebSocket 客户端基类（连接/心跳/接收循环） |
| `apps/voice/services/asr_stream_client.py` | 49 | Gateway ASR 流式客户端 |
| `apps/voice/services/tts_stream_client.py` | 76 | Gateway TTS 流式客户端 |
| `apps/voice/services/tts_pipeline_manager.py` | 149 | TTS 播报队列（安慰/回复/错误 3 级） |
| `apps/voice/services/tts_router.py` | 168 | 跨设备 TTS 广播 + HA 音箱路由 |
| `apps/voice/services/voice_pipeline.py` | 169 | 语音推理管道编排 + barge-in 打断 |
| `apps/voice/services/response_decision_service.py` | 184 | 8 级响应决策链 + LLM 意图分类 |
| `apps/voice/services/utterance_aggregator.py` | 98 | 多段话语缓冲聚合（3 秒超时） |
| `apps/voice/services/voice_session_service.py` | 97 | Redis 会话管理 + 频率限制 |
| `apps/voice/services/voice_persist_service.py` | 136 | PCM->WAV + MinIO + record_only_ambient |

---

## 十三、Redis 键参考

| 键模式 | TTL | 用途 |
|--------|-----|------|
| `voice:session:{uid}` | 3600s | 会话状态 JSON |
| `voice:active_conv:{uid}` | `VOICE_ACTIVE_CONV_TTL` | 活跃对话标记（决策链第 5 级） |
| `voice:audio_chunks:{uid}:{seg}` | `VOICE_AUDIO_CACHE_TTL` | PCM 帧缓存 |
| `voice:llm_rate:{uid}` | 60s | LLM 频率限制（60 次/分） |
| `voice:recent_speakers:{uid}` | 60s | 说话人集合（决策链第 6 级） |
| `voice:ws_connect_rate:{uid}` | 60s | WS 连接频率限制（10 次/分） |
| `voice:ambient_conn:{uid}` | 3600s | 设备独占连接注册 |
| `user:{uid}:inference_task` | -- | 推理任务注册（InferenceService） |

---

## 十四、配置参数参考

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `VOICE_AMBIENT_AGGREGATE_TIMEOUT` | 3s | 聚合器静默超时 |
| `VOICE_AMBIENT_MAX_BUFFER_SIZE` | 10 | 聚合器最大缓冲段数 |
| `VOICE_AMBIENT_SESSION_TTL` | 3600s | ambient 会话 Redis TTL |
| `VOICE_DECISION_USE_LLM` | -- | 是否启用 LLM 意图分类 |
| `VOICE_DECISION_LLM_TIMEOUT` | -- | LLM 意图分类超时 |
| `VOICE_DECISION_LLM_THRESHOLD` | -- | LLM 置信度阈值 |
| `VOICE_DEFAULT_WAKE_WORDS` | -- | 默认唤醒词列表 |
| `VOICE_MAX_SEGMENT_DURATION` | -- | 语音段最大时长 |
| `VOICE_TTS_ENABLED` | -- | TTS 总开关 |
| `VOICE_TTS_VOICE` | -- | TTS 声音 |
| `VOICE_TTS_COMFORT_DELAY` | -- | 安慰语音触发延迟 |
| `VOICE_TTS_COMFORT_TEXTS` | -- | 3 条安慰语音文本 |
| `VOICE_TTS_ERROR_TEXT` | -- | 错误播报文本 |
| `VOICE_TTS_SEGMENT_GAP` | -- | TTS 段间间隔 |
| `VOICE_TTS_TIMEOUT` | -- | TTS 单次播放超时 |
| `VOICE_IDLE_TIMEOUT` | -- | 空闲超时（ambient 跳过） |
| `VOICE_ASR_SPEECH_PAD_MS` | -- | ASR VAD 静默尾部保护 |
| `VOICE_ASR_LANGUAGE` | -- | ASR 语言 |
