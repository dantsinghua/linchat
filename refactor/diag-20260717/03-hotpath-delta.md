# 增量热路径分析 — R2 rediagnosis（batch-08~12 delta）

> 生成时间：2026-07-17
> 范围：仅今日 loop 改动后的 ambient 语音新链路 + batch-11/12 基础设施
> 方法：静态源码复核（Gateway 离线，无法实测；延迟为代码常量静态推算，量级待压测）
> 先验：docs/legacy-and-debts.md 二节（5s SLO）、refactor/batches/batch-08~12-plan.md
> 证据基准：main HEAD 已含 batch-04~28

---

## 执行摘要

- **静态推算 ambient/小爱端到端延迟下限**：**≈ 5.5–7s（仍超 5s SLO）**。答案 LLM 产出首 token **之前**的固定串行等待已达 **~3–4s**：ASR 静默 pad 2.0s + 聚合窗口 1.5s + 决策 LLM 分类 ≤2.0s（三者串行，见 §3）。
- **下一瓶颈（Top1）**：**决策 LLM 意图分类**（`response_decision_service.py:66,100`）——在答案生成前插入一次**完整 httpx LLM 往返**（timeout 2.0s）+ 上下文召回 IO，纯串行、阻塞 pipeline 启动。这是 batch-08~12 **未触及**的新增最大瓶颈。
- **下一瓶颈（Top2）**：**小爱音响不在 batch-09/10 优化路径上**。当 `tts_output_device=="ha_speaker"` 时，小爱走 `_try_ha_speaker_tts`（`voice_pipeline.py:229-230`）——在 pipeline 完成 **之后** 用 full_response 整体下发 HA，仍是「等全部 token→整体合成」；batch-09 增量流式 / batch-10 预连接只惠及浏览器 tts_router 路径，**对小爱可听延迟零收益**，且浏览器 Gateway TTS 的整段合成在 `finally.wait_idle()`（:216）里**串行阻塞**了小爱下发。
- **新风险数：5**（详见 §5）。其中 P1 = 2（小爱串行浪费、决策 LLM 阻塞），P2 = 3（Redis 阻塞池延迟传导、model_config 缓存跨线程竞态+明文驻留、埋点漏跳致 SLO 归因失真）。
- **埋点覆盖缺口**：聚合 1.5s 等待、speaker identify、决策 LLM 三段（均在 `latency_start` 之前）**未进 `hops`**，`hop_sum`/`total_from_pipeline_ms` 系统性漏计 ~3–4s；仅 `total_from_vad_ms` 兜住，但依赖 segment_id 对齐（自带 `_approx` 风险，voice_latency.py:18-20）。

---

## 1. ambient 新链路实测链路图（静态）

```
reSpeaker → WiFi桥(016) → VoiceConsumer ASR-WS流式(asr_stream_client.py)
  │  VOICE_ASR_SPEECH_PAD_MS=2000  ← 静默 pad，段末固定 ~2s 才 finalize
  ▼
vad.speech_end (consumer_events.py:50)  ← latency_anchor speech_end
  ▼
_identify_ambient_speaker (consumer_events.py:120)  ← 【串行·阻塞】speaker_service.identify_from_pcm，整段PCM→Gateway HTTP RT
  ▼
UtteranceAggregator.add → _on_timeout sleep(1.5s) (utterance_aggregator.py:77)  ← 【固定等待 1.5s】VOICE_AMBIENT_AGGREGATE_TIMEOUT
  ▼
_on_utterance_aggregated (consumer_session.py:155)
  ├─ 未识别 → RECORD_ONLY 直接 return（不回复）
  └─ 已识别 → decide() (response_decision_service.py:47)
        ├─ _is_tts_echo (redis)
        ├─ _load_wake_words (batch-12: 60s TTL 缓存, :175)
        └─ VOICE_DECISION_USE_LLM=true → _classify_intent_llm (:100)  ← 【串行·阻塞·新瓶颈】
              ├─ _fetch_intent_context: find_latest×5 + retrieve_memories (:129)
              └─ httpx LLM RT, timeout=VOICE_DECISION_LLM_TIMEOUT=2.0s
  ▼ (decision==RESPOND)
_start_voice_pipeline → VoicePipeline.run_pipeline → _run_inner (voice_pipeline.py:92)
  │  latency_start(t0)  ← 埋点起点在此，前面 3~4s 不计入 hops
  ├─ preconnect(batch-10, 默认flag OFF): begin_stream() 与推理并行建连
  ├─ ambient+FLAG: AmbientLightPipeline.stream (batch-08 轻量直调, ambient_light_service.py:40)
  │     └─ httpx /chat/completions stream, 跳过 LangGraph/工具/记忆召回
  ├─ 循环内 _split_sentences → feed_text (batch-09 增量, :162-172) → Gateway TTS-WS → on_audio
  │     └─ ambient on_audio = tts_router.send_binary → 【浏览器】(setup_tts:265)
  └─ finally: wait_idle()+shutdown() (:216) ← 等浏览器 Gateway TTS 整段合成完
  ▼
_try_ha_speaker_tts (voice_pipeline.py:229)  ← 【小爱真正路径】pipeline 完成后
  └─ send_to_ha_speaker(full_response) (tts_router.py:107) → xiaomi_miot.intelligent_speaker 整段文本
        → 小爱本地 TTS 合成 + 播放
```

**关键结构性发现**：小爱音响链路与浏览器 TTS 链路是**两条独立出口**。batch-08（轻量推理）在小爱路径上有效；batch-09/10（增量流式+预连接）**只作用于浏览器 on_audio**，小爱走 §Top2 的 full_response 整体下发。

---

## 2. batch-08~10 静态审查（串行等待 / 竞态 / 埋点）

### 2.1 batch-08 轻量推理（有效，惠及小爱）
- `ambient_light_service.py:40` 直调 Gateway，跳过 PromptBuilder/记忆召回，是小爱路径上唯一削减答案 LLM 时延的改动。✅ 语义对齐红线（复用 `get_active_model` SM4 解密 :48、`map_llm_exception` :98、`create_first_token_messages` :72、隔离粒度仅 user_id :144）。
- 残留串行：`_build_messages` 先 `get_active_model`（:48，batch-12 缓存命中）再 `message_repo.find_latest_by_user`（:144）——两者无依赖但**未并行**（batch-12 只并行了完整 Agent 的 PromptBuilder，未覆盖此轻量路径）。收益极小（~10ms），记为可选优化。

### 2.2 batch-09 增量流式 TTS（浏览器有效 / 小爱无效）
- `voice_pipeline.py:162-172` 循环内按句 `feed_text` + `tts_pipeline_manager.py:56-133` 单条常驻会话，connect 只付一次。设计正确、与 LLM 重叠。✅
- **但对小爱不生效**：ambient 的 on_audio 固定为 `tts_router.send_binary`（浏览器）。小爱不消费该音频流。

### 2.3 batch-10 预连接 + 优雅关闭
- 预连接默认 **flag OFF**（`VOICE_TTS_PRECONNECT_ENABLED=false`，voice.py），故当前生产链路**未启用**，SLO 无实际收益直到灰度开启。
- `_current_tts` 延迟认领（tts_pipeline_manager.py:104,117）正确隔离了 comfort×流式竞态——但 **ambient 已 `_comfort_enabled=False`（voice_pipeline.py:276）**，该竞态窗口在小爱/ambient 路径根本不存在，仅 voice_chat 相关。
- 优雅关闭 `ws_client_base.py:12-27`（close-first + code=1000）方向正确，消 1006 噪声。⚠️ 1006→1000 根因为静态推断，Gateway 离线未运行时验证（batch-10 计划 §7 已登记）。

### 2.4 barge-in × 增量发送
- barge-in 经 `run_pipeline`（:78-87）cancel → `mgr.cancel()`（tts_pipeline_manager.py:149）→ `cancel_task(_stream_task)`（:164）。预连接空转期 stream_task park 在 `_stream_queue.get()`，可被 cancel。✅ 无残留连接。
- `_pending_text` 缓冲（consumer_session.py:201-209）串行重放，无并发叠加。✅

### 2.5 埋点覆盖完整性（team lead 专项）
`latency.summary.hops` **缺 3 跳**（均在 `latency_start` 之前发生）：
| 缺失跳 | 证据 | 量级 | 现状 |
|--------|------|------|------|
| 聚合静默等待 | utterance_aggregator.py:77 sleep(1.5s) | ~1.5s 固定 | 只 log `ambient.aggregation.flush.wait_ms`，未 `latency_record` |
| speaker identify | consumer_events.py:120-133 Gateway RT | ~数百ms | 只 log `speaker.identify.duration_ms` |
| 决策 LLM 分类 | response_decision_service.py:100 | ≤2.0s | 只 log `decision.decide/llm_classify` |
→ `hop_sum` 与 `total_from_pipeline_ms` 系统漏计 ~3–4s；`delta_pct` 失真。`total_from_vad_ms`（voice_latency.py:110）理论兜底，但聚合模式 segment_id 可能错位（自述 `_approx`，:18-20）。**建议**：把三段补入 `latency_record`（用同一 seg），或至少将聚合/decide 纳入 summary，否则 5s SLO 归因失真。

---

## 3. 5s SLO 静态延迟下限推算

固定/串行项（ambient 已识别说话人，非唤醒词直答；均来自代码常量）：

| 阶段 | 常量/证据 | 是否阻塞下游 | 静态量级 |
|------|-----------|-------------|---------|
| ASR 静默 pad | VOICE_ASR_SPEECH_PAD_MS=2000 | 与说话重叠，段末仍固定 pad | ~部分固定 |
| speaker identify | consumer_events.py:133 Gateway RT | 阻塞聚合后决策 | ~0.3–0.8s（待压测）|
| **聚合静默窗口** | VOICE_AMBIENT_AGGREGATE_TIMEOUT=1.5 | **固定阻塞** | **1.5s** |
| **决策 LLM 分类** | VOICE_DECISION_LLM_TIMEOUT=2.0 | **串行阻塞 pipeline 启动** | **~0.8–2.0s** |
| 答案 LLM（batch-08 轻量至可下发） | LLM_CALL_TIMEOUT=60，实测 kimi 首token~1s | 流式 | ~1.5–2s |
| 浏览器 Gateway TTS 整段合成 | finally wait_idle voice_pipeline.py:216 | **串行阻塞小爱下发** | ~1–2s（对小爱纯浪费）|
| HA 下发 + 小爱本地 TTS+播放 | tts_router.py:107 httpx→xiaomi_miot | 阻塞 | ~1s+（待压测）|

**下限（小爱可听）≈ 1.5(聚合) + ~1(决策LLM典型) + ~2(答案LLM) + ~1(浏览器TTS浪费) + ~1(HA+小爱) ≈ 6.5s**，即使决策 LLM 命中快路径也 **≥5.5s**，**当前设计静态下限仍超 5s SLO**。

**达到 5s 的必砍项（按收益）**：
1. **决策 LLM 移出关键路径**：改为「先 pipeline、意图分类并行/事后」或对高置信规则（question/active_conversation）短路跳过 LLM。省 ~0.8–2s。
2. **小爱直连流式**：让小爱走增量 TTS（batch-09 的 feed_text 直接对接 HA 流式接口），删除 `finally.wait_idle` 对小爱的串行阻塞 + 浏览器整段合成浪费。省 ~1–3s。
3. **聚合窗口自适应**：句末标点/疑问句即时 flush，不等满 1.5s。省 ~0.5–1.5s。
4. 开启 batch-10 预连接 flag（当前 OFF）。

---

## 4. batch-11/12 链路收益点与新风险

### 4.1 batch-11 Redis 共享池（实现为 BlockingConnectionPool）
- 计划书写普通 ConnectionPool（超限报错），**实际落地为 `BlockingConnectionPool`（redis.py:66,76，timeout=10s）**——语义偏差：超限**阻塞≤10s**而非快速失败。
- **收益点**：消除 14 调用点/33 处未 aclose 的连接泄漏；ASGI 单 loop 复用 1 池（redis.py:57-84）。✅ 对语音链路的多次 redis 调用（check_llm_rate_limit、is_tts_echo、session、unknown_label incr/hset）连接开销下降。
- **新风险 R3（P2）**：池耗尽时 BlockingPool 让语音链路的 redis 调用**静默阻塞至多 10s**（而非报错暴露），延迟被隐性放大且不计入任何 hop。家庭低并发下概率低，但 pubsub 长连接（每 SSE 订阅 + cancel_monitor 各占 1）叠加时存在尾延迟。

### 4.2 batch-12 gather 并行 + TTL 缓存
- **gather 并行**（prompt.py:29-36）仅在 `build_prompt_preamble`（完整 Agent/voice_chat 路径）。**ambient/小爱走 batch-08 轻量路径，绕过 PromptBuilder，gather 零收益**。
- **model_config 60s TTL 缓存**惠及 8 处调用方，含小爱路径 `ambient_light_service.py:48` 与决策 LLM `response_decision_service.py:87`——省一次 DB 查询 + SM4 解密（~数十ms/调用）。✅ 小爱路径唯一受益点。
- **新风险 R4（P2）**：`get_active_model` 为**同步函数经 `sync_to_async` 在线程池执行**，`_model_cache` 普通 dict 被多线程读改（check-TTL-then-set 非原子）→ 良性竞态（最坏重复查库一次），但**缓存持有 SM4 解密明文 api_key 驻留内存 60s**（计划 §7 已知）。
- **新风险 R5（P2）**：直接 ORM 改 ModelConfig（MEMORY.md 记录的既有换模型方式）绕过 `update_model` 失效钩子 → 最长 60s 用旧模型/旧 key，无手动 flush 入口。

---

## 5. 新风险清单（共 5）

| # | 风险 | 级别 | 证据 | 影响 |
|---|------|------|------|------|
| R1 | 决策 LLM 分类串行阻塞答案生成 | P1 | response_decision_service.py:66,100 | +0.8~2s，5s SLO 首要障碍 |
| R2 | 小爱走 full_response 整体下发 + 浏览器 TTS 整段合成串行浪费 | P1 | voice_pipeline.py:216,229；tts_router.py:107 | batch-09/10 对小爱零收益，反增串行等待 |
| R3 | BlockingConnectionPool 池耗尽静默阻塞≤10s | P2 | redis.py:66,76 | 语音链路隐性尾延迟，不计 hop |
| R4 | model_config 缓存跨线程竞态 + 明文 key 驻留 60s | P2 | services.py TTL 缓存；sync_to_async 线程池 | 良性竞态；安全面轻微扩大 |
| R5 | ORM 直改模型绕过缓存失效，60s 陈旧 | P2 | batch-12-plan §7；MEMORY.md 换模型方式 | 换模型/换 key 后最长 60s 不生效 |

---

## 6. Open Questions

1. **Q1**：决策 LLM 分类能否对「疑问句/active_conversation」高置信规则**短路**，仅在低置信时才调 LLM？（当前 :63-70 LLM 先于 question 规则，顺序可调）
2. **Q2**：小爱是否有流式文本接口可对接 batch-09 的 `feed_text`，从而删除 `finally.wait_idle` 对小爱的串行阻塞？（需 HA xiaomi_miot 能力确认）
3. **Q3**：聚合窗口 1.5s 是否可做「句末标点即时 flush」自适应，而非固定 sleep？
4. **Q4**：batch-11 落地为 BlockingConnectionPool 与计划书（普通 Pool 报错语义）不一致——是否有意改为阻塞语义？max_connections=50 是否覆盖 pubsub 峰值？
