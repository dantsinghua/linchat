# 增量热路径分析 — R2 rediagnosis（batch-29~32 delta）

> 生成时间：2026-07-17
> 范围：仅 batch-29~32 对 voice 语音链路的改动 delta（git diff diag-20260717..HEAD -- backend/apps/voice/）
> 方法：静态源码复核（Gateway 离线，voice_e2e_p50_ms 无实测；延迟为代码常量静态推算，量级待压测）
> 先验：refactor/diag-20260717/03-hotpath-delta.md（R1，识别 R1~R5 五风险）、docs/legacy-and-debts.md（5s SLO）
> 证据基准：main HEAD 已含 batch-04~36

---

## 执行摘要

- **batch-29~32 覆盖度结论**：R1 分析中列出的「达到 5s 的必砍项」前 3 项 **已全部落地对应优化 batch**，但**全部为 dark-launch（flag 默认 false，生产未启用）**：
  - R1 必砍项①「决策 LLM 移出关键路径」→ **batch-30** `VOICE_DECISION_SHORTCIRCUIT_ENABLED`（默认 false，voice.py:141）
  - R1 必砍项②「小爱直连/删除 wait_idle 串行阻塞」→ **batch-31** `VOICE_HA_PARALLEL_TTS_ENABLED`（默认 false，voice.py:64）
  - R1 必砍项③「聚合窗口自适应 flush」→ **batch-32** `VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED`（默认 false，voice.py:109）
  - R1 §2.5 埋点三缺跳 → **batch-29** speaker_identify/aggregation/decision 补 `latency_record` + 新增 `delta_vad_pct`（voice_latency.py:120）
- **剩余可成新 perf batch 的代码层串行点：1 个（弱候选）**——ASR 段末静默 pad 固定 2.0s（`VOICE_ASR_SPEECH_PAD_MS=2000`，voice.py:67），是**唯一未被任何 batch 触及的大额固定串行延迟**，但属 ASR 侧调参（截断/准确率风险），**不建议纯静态立 batch，需实测 + 现场准确率验证**。
- **其余剩余串行点（speaker identify 串行、决策上下文 2 次 IO）均为 Gateway RT 绑定或已被 batch-30 短路规避**，代码层收益 ≤ 数十 ms 或受阻于 Gateway 离线无法定位量级。
- **是否建议新 perf batch**：**否（本轮）**。批 29~32 已把 R1 识别的主要串行等待全部转成可灰度的代码路径；**当前瓶颈是「优化未启用」+「无实测数据」而非「缺优化代码」**。下一步应是 ops 灰度开 flag + Gateway 恢复后测 voice_e2e_p50_ms，而非再写新代码 batch。
- **受阻于实测数据缺失**：是。ASR pad、speaker identify、HA→小爱本地 TTS 播放三跳量级均为 Gateway/设备 RT，Gateway 离线状态下无法定位真实瓶颈占比，静态只能给存在性判断。

---

## 1. batch-29~32 落地静态复核（是否真的削减了 R1 串行等待）

### 1.1 batch-30 决策 LLM 短路（对应 R1-Top1 / 风险 R1）
- `response_decision_service.py:63-79`：flag 开启时，`is_active_conversation`→`multi_speaker`→`question_detected` 三条高置信规则**先于** LLM 短路 RESPOND/RECORD_ONLY，仅「非唤醒/非活跃/非疑问的歧义声明句」才落到末位 `_classify_intent_llm`（:75）兜底。✅ 语义正确移出关键路径。
- **旧路径逐字节保留**：flag=false 分支（:82-88）维持「LLM 先于规则」旧顺序，dark-launch 无行为漂移。✅
- **残留**：短路命中前仍串行 `await is_active_conversation`（redis）+ 可能 `_get_recent_speaker_count`（redis）+ `_check_question_features`（纯 regex，:191 无 IO）。均为廉价 redis RT，量级远小于被砍掉的 LLM 往返。命中兜底 LLM 时 `_fetch_intent_context`（:148）仍是 `find_latest_by_user(limit=5)` + `retrieve_memories` **两次串行 IO**——但这只在低置信歧义句发生，已非主路径。
- **结论**：R1-Top1（决策 LLM 阻塞 ~0.8~2s）在 flag 开启后**基本消除**。flag 未开则收益为 0。

### 1.2 batch-31 HA 下发并行（对应 R1-Top2 / 风险 R2）
- `voice_pipeline.py:207-212`：flag 开启且 ambient 时，`full_response` 就绪即 `asyncio.create_task(_try_ha_speaker_tts)` 并注册到 `_active_ha_tasks`，与 `finally.wait_idle()`（浏览器 Gateway TTS 整段合成）**时间重叠**；`finally` 后（:240-249）`await ha_task`，通常已就绪。✅ 削除了 R1-Top2「小爱等浏览器 TTS 整段合成」串行浪费。
- **barge-in 安全**：`cancel()` 中 `_active_ha_tasks.pop().cancel()`（:66-68）best-effort 取消；已发出的 POST 无法收回（注释自陈 §4 残留），家庭低频打断可接受。✅
- **残留（非 latency）**：即使小爱走 HA，浏览器 Gateway TTS 整段合成仍在 `finally.wait_idle` 执行——**对纯小爱场景是 compute 浪费但已不在小爱可听关键路径**（并行后小爱不再等它）。属资源浪费非延迟，优先级低。
- **结论**：R1-Top2 延迟部分在 flag 开启后消除；compute 浪费残留，非 SLO 障碍。

### 1.3 batch-32 聚合自适应 flush（对应 R1 必砍项③ / Q3）
- `utterance_aggregator.py:63-68`：flag 开启且 `_is_utterance_complete(text)`（结尾字符 ∈ `。！？!?…`，:79-86）时取消定时器**即时 flush**，不等满 1.5s；逗号/无标点仍走 `_on_timeout` 1.5s 兜底。✅ 正确的句末即时触发。
- `flush_reason` 埋点（:118）区分 sentence_end / timeout / max_buffer / manual，便于灰度后测「即时 flush 命中率」。✅
- **残留风险**：`_is_utterance_complete` 只看**本条 ASR text 结尾**，若 ASR 分片把句号切到下一片，判定滞后一拍——但最坏退回 1.5s 兜底，无负收益。
- **结论**：R1 必砍项③在 flag 开启后省 0.5~1.5s（依说话内容）。

### 1.4 batch-29 埋点补跳（对应 R1 §2.5）
- speaker_identify 三处出口 + aggregation + decision 补 `latency_record`（consumer_events.py:127/145/161），并新增 `delta_vad_pct`（voice_latency.py:116-120）以 `total_from_vad_ms` 为基准衡量整链覆盖率，`delta_pct` 保留兼容 batch-07 脚本。✅ R1 §2.5 「hop_sum 系统漏计 ~3~4s」的归因失真已修复度量口径。
- **注意**：`delta_pct`（对 pipeline t0 段）在补跳后会失真变负（hop_sum 跨越 t0 之前），需以 `delta_vad_pct` 为准判读——这是口径变更，读盘脚本需更新。

---

## 2. 剩余可优化点扫描（代码层 vs 需实测）

### 2.1 代码层仍可动（弱候选）

| # | 剩余点 | 链路位置 | 证据 | 静态量级 | 是否建议成 batch | 风险 |
|---|--------|---------|------|---------|-----------------|------|
| A | ASR 段末静默 pad 固定 2.0s | reSpeaker→ASR finalize，聚合前 | voice.py:67 `VOICE_ASR_SPEECH_PAD_MS=2000` | ~部分固定 2s，**唯一未触及大额** | **否（弱）**：需现场准确率验证 | 高：调小易致句尾截断/误 finalize，损 ASR 准确率 |
| B | speaker identify 串行阻塞聚合路由 | consumer_events.py:100→104 | :135 `identify_from_pcm` Gateway HTTP RT | ~0.3~0.8s（Gateway RT，待压测） | 否：与 per-speaker 路由强耦合 | 中：并行需重构 `_get_or_create_aggregator` 依赖 uid 的路由，收益/风险比差 |
| C | 决策兜底 `_fetch_intent_context` 2 次串行 IO | response_decision_service.py:158 | find_latest×1 + retrieve_memories×1 | 数十 ms，且已被 batch-30 短路规避 | 否：非主路径，可并行但收益微 | 低 |
| D | 浏览器 Gateway TTS 整段合成 compute 浪费（纯小爱场景） | voice_pipeline.py finally.wait_idle | batch-31 已移出小爱关键路径 | 非延迟，仅 compute | 否 | 低 |

**A 是唯一有意义的剩余大额固定延迟**，但它是 ASR 侧调参而非架构串行，砍它需要「自适应 pad / 疑问句提前 finalize」这类带准确率风险的改动，**静态无法判断安全阈值，必须 Gateway 恢复后用真实音频测截断率**。

### 2.2 需 Gateway 恢复后实测才能定位（代码层无从下手）

- **ASR pad / speaker identify / 答案 LLM 首 token / HA→小爱本地 TTS 合成播放** 四跳的真实占比——全部是 Gateway/设备 RT，Gateway 离线状态下 `voice_e2e_p50_ms` 无数据，静态只能给「存在性」不能给「量级」。
- batch-29 埋点补齐后，**只要 Gateway 恢复 + 灰度开 flag**，`hops` + `delta_vad_pct` 即可直接给出各跳 P50，届时才能判断「A（ASR pad）是否真是下一瓶颈」——**当前立 batch 属盲目优化**。

---

## 3. 语音 E2E 各跳静态耗时（flag 全开假设下的下限重估）

假设 batch-30/31/32 flag 全部灰度开启（当前默认 false），相比 R1 §3 下限的变化：

| 阶段 | 证据 | flag 前（R1） | flag 全开后（本轮静态） | 削减来源 |
|------|------|--------------|----------------------|---------|
| ASR 段末 pad | voice.py:67=2000 | ~部分固定 2s | **不变 ~2s** | 未触及（候选 A） |
| speaker identify | consumer_events.py:135 | ~0.3~0.8s 串行 | 不变（仍串行） | 未触及（候选 B） |
| 聚合静默窗口 | utterance_aggregator.py:63 | 固定 1.5s | **句末即时 ~0，兜底 1.5s** | batch-32 |
| 决策 LLM 分类 | response_decision_service.py:75 | 串行 ~0.8~2s | **高置信短路 ~0**（redis 廉价） | batch-30 |
| 答案 LLM（batch-08 轻量） | ambient_light_service.py | 流式 ~1.5~2s | 不变 | —— |
| 浏览器 TTS 对小爱串行浪费 | voice_pipeline.py:207 | 串行 ~1~2s | **并行 ~0**（重叠 wait_idle） | batch-31 |
| HA 下发 + 小爱本地 TTS 播放 | tts_router.py | ~1s+ | 不变 | 设备侧 |

**flag 全开静态下限（小爱可听）≈ 2(ASR pad) + ~0.5(identify) + ~0(聚合命中) + ~0(决策短路) + ~1.5(答案LLM) + ~1(HA+小爱) ≈ 5s**，**首次逼近 5s SLO 边界**（R1 flag 前下限 ≈ 5.5~6.5s）。
**决定性变量收敛到两跳**：ASR pad（2s，候选 A）与 HA→小爱本地播放（~1s，设备侧）。二者合计 ~3s 占了下限的 60%，**且都无法靠改 backend 代码解决**（ASR 侧调参带准确率风险 / 小爱本地 TTS 是设备行为）。

---

## 4. 下一个最大瓶颈判定

**结论：batch-29~32 已覆盖答案生成前的主要软件串行等待（决策 LLM、聚合窗口、HA/浏览器 TTS 串行）。剩余下限主要由两跳构成，均非可安全靠代码消除的软件串行：**

1. **ASR 段末 pad 2.0s（候选 A）**——最大剩余固定延迟，但属 ASR 调参，砍它有截断风险，**必须实测截断率后才能决定**，不是纯代码 batch 能安全兑现的。
2. **HA→小爱本地 TTS 合成播放 ~1s（设备侧）**——xiaomi_miot 整段文本本地合成，backend 不可控。

**因此：剩余代码层优化空间有限，进一步压缩需实测数据定位 + ASR 侧调参验证，而非新的 backend 重构 batch。**

---

## 5. 建议（非 batch，属 ops / 后续）

1. **优先灰度开 batch-30/31/32 flag**（非代码工作）：三个 dark-launch 优化已就绪，收益未兑现纯因 flag=false。建议 Gateway 恢复后按 30→32→31 顺序灰度，用 batch-29 的 `flush_reason` / `delta_vad_pct` / `hops` 观测。
2. **Gateway 恢复后先测 `voice_e2e_p50_ms` 基线**，确认 ASR pad 是否真为下一瓶颈，再决定候选 A 是否立项——**避免盲目立 ASR 调参 batch**。
3. 候选 B/C/D 收益 ≤ 数十 ms 或非延迟，**本轮不建议立 batch**。

---

## 6. Open Questions（承接 R1，未解）

1. **Q1**（承 R1-Q2）：小爱是否有流式文本接口可对接 batch-09 `feed_text`？若有，可进一步削 HA→播放的整段合成延迟——需 HA xiaomi_miot 能力确认，非 backend 静态可判。
2. **Q2**：ASR pad 2.0s 能否做「疑问句/句末标点提前 finalize」的自适应？需真实音频测截断率，Gateway 离线无法验证。
3. **Q3**：batch-30/31/32 灰度默认何时转 default-on？（当前全 false，SLO 收益悬空。）
