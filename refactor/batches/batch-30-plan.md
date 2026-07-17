# Batch-30 执行计划：决策 LLM 意图分类移出关键路径（高置信规则短路）

> 生成时间：2026-07-17 | 类型：refactor/performance | 优先级：P1 | 风险：high
> 预估：3 文件（实为 4，见 §7）/ ~120 行 / 1 session
> 依赖：batch-29 = COMPLETED ✅（decision_llm 埋点已就位，consumer_session.py:217）
> SLO 影响：blocks_slo=voice_end_to_end_5s（R1，5s SLO 首要障碍，省 ~0.8~2s）

## 1. 任务理解（一句话）

ambient 决策链当前把「完整 httpx 决策 LLM 往返（timeout 2.0s）+ 上下文召回 IO」放在
`decide()` 的**规则之前**（response_decision_service.py:63-70），先于 active_conversation
与 question 规则执行、纯串行阻塞 pipeline 启动；本 batch **仅调整执行顺序**——把 LLM 块下移到
question 规则**之后**，让高置信 RESPOND 规则先短路，仅对「非唤醒/非活跃/非疑问」的歧义声明句
才调 LLM，用新 flag `VOICE_DECISION_SHORTCIRCUIT_ENABLED` 灰度控制、可运行时回退。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | backend/apps/voice/services/response_decision_service.py | 259 | +15 -8 | 重排 decide() + flag 门控 | 高 | 低（无未用 import；3 处 E701 既有，非本批引入，不顺手改） |
| 2 | backend/core/settings/voice.py | 136 | +4 | 新增 flag | 低 | 低 |
| 3 | backend/tests/voice/test_response_decision.py | 1171 | +40 | 新增短路矩阵测试 | 低 | 低 |
| 4 | backend/tests/voice/test_response_decision_llm.py（**scope 外，必须纳入，见 §7**） | 1463 | +30 -10 | 更新 3 处编码旧顺序的测试 + 补 flag on/off | 中 | 低 |

> 无文件 > 300 行硬限制触发（response_decision_service 259 行；两个 test 文件是测试，不适用 300 行拆分规则）。

## 3. 现状耗时分析

### 3.1 当前决策链顺序（response_decision_service.py:47-79）
```
1. empty            :51-52  → RECORD_ONLY
2. tts_echo         :54-55  → DISCARD           （前置 echo 抑制，不可绕过）
3. emergency_stop   :56-57  → STOP
4. wake exact/fuzzy :58-62  → RESPOND
5. 【LLM 意图分类】 :63-70  → 若 conf≥0.75 采纳   ★ 串行阻塞点（httpx RT ≤2.0s + _fetch_intent_context: find_latest×5 + retrieve_memories）
6. active_conv      :71-72  → RESPOND
7. multi_speaker    :73-76  → RECORD_ONLY        （仅 not speaker_identified 且 recent≥2）
8. question         :77-78  → RESPOND
9. default          :79     → RECORD_ONLY
```

### 3.2 耗时贡献（batch-29 埋点 `decision_llm` hop = decide() 全程，consumer_session.py:217）
- LLM 命中路径：`_classify_intent_llm`（:81-126）一次 httpx `/chat/completions`，timeout=`VOICE_DECISION_LLM_TIMEOUT=2.0s`，叠加 `_fetch_intent_context`（:129，message_repo.find_latest×5 + MemoryService.retrieve_relevant_memories）。
- 03-hotpath-delta §3 静态量级：**~0.8~2.0s**，串行阻塞 pipeline `latency_start`。
- **关键落差**：LLM 现处 step 5，**先于** active_conv(6)/question(8) 执行 → 即便一句明确疑问句也要先付一次完整 LLM 往返。目标：让 6、8 先短路 RESPOND，LLM 退居 step 8 之后仅兜歧义声明句。
- **收益边界（诚实）**：短路只惠及「疑问句 / 活跃对话」输入；**普通声明句（非疑问、单说话人、非活跃）仍走 LLM**（见 §5 行为边界 BC4），此类输入的 decision 耗时不变。故 metric「高置信场景 P50 < 0.1s」仅对 question/active_conv 成立，不代表全 ambient。

## 4. 详细改动计划

### 文件 2（先做，被 §文件1 引用）: backend/core/settings/voice.py

#### 改动 2.1 — 新增短路 flag（插在 :121 `VOICE_DECISION_LLM_TIMEOUT` 之后）
```python
# batch-30：高置信规则短路——active_conversation / question 先直接 RESPOND，
# LLM 意图分类下移为歧义声明句兜底，移出 ambient 关键路径。
# 关=保持旧顺序（LLM 先于规则，行为完全不变），首选灰度回滚手段。
VOICE_DECISION_SHORTCIRCUIT_ENABLED = (
    os.getenv("VOICE_DECISION_SHORTCIRCUIT_ENABLED", "false").lower() == "true"
)
```
- 理由：与 batch-10 `VOICE_TTS_PRECONNECT_ENABLED` 默认 false 的灰度惯例一致；default=false 使合并**行为完全不变**，安琳观测 batch-29 埋点后再灰度置 true。**默认值待安琳确认（§7）**。

### 文件 1: backend/apps/voice/services/response_decision_service.py

#### 改动 1.1 — 从 decide() 移除 :63-70 的 LLM 前置块
当前 :63-70：
```python
if mode == "ambient":
    from django.conf import settings as django_settings
    if django_settings.VOICE_DECISION_USE_LLM:
        llm_result = await self._classify_intent_llm(text, user_id)
        if llm_result is not None:
            decision, reason, confidence = llm_result
            if confidence >= django_settings.VOICE_DECISION_LLM_THRESHOLD:
                return decision, f"llm_{reason}"
```
删除此处；逻辑整体后移（见 1.2）。

#### 改动 1.2 — 在 question 检查（:77-78）之后、default（:79）之前重排决策
将 :63-79 区间改为（保留规则链内部相对顺序 active_conv→multi_speaker→question 不变，仅把 LLM 移到末尾）：
```python
from django.conf import settings as django_settings
_shortcircuit = getattr(django_settings, "VOICE_DECISION_SHORTCIRCUIT_ENABLED", False)

# 高置信 RESPOND 规则：flag 开启时先短路，跳过 LLM（移出关键路径）
if await voice_session_service.is_active_conversation(user_id):
    return DecisionResult.RESPOND, "active_conversation"
if not speaker_identified:
    recent = await self._get_recent_speaker_count(user_id)
    if recent >= 2:
        return DecisionResult.RECORD_ONLY, "multi_speaker"
if self._check_question_features(text):
    return DecisionResult.RESPOND, "question_detected"

# 歧义声明句才调 LLM（原 step 5，现降为末位兜底）
if mode == "ambient" and django_settings.VOICE_DECISION_USE_LLM:
    llm_result = await self._classify_intent_llm(text, user_id)
    if llm_result is not None:
        decision, reason, confidence = llm_result
        if confidence >= django_settings.VOICE_DECISION_LLM_THRESHOLD:
            return decision, f"llm_{reason}"
return DecisionResult.RECORD_ONLY, "default"
```
- **flag=false 时必须等价旧行为**：需保留旧顺序分支。实现建议——用 `if _shortcircuit:` 包裹「短路块（active_conv/question 先于 LLM）」，`else:` 分支保持 :63-79 原顺序（LLM 先于规则）。两条路径共享 `_classify_intent_llm`，避免重复代码。执行者按此语义落地（保留 else 旧序，勿只留新序）。
- 理由：flag=false → 行为逐字节等价当前；flag=true → active_conv/question 短路 RESPOND，LLM 退居声明句兜底。reason 字符串（llm_*/active_conversation/question_detected/multi_speaker/default）全部保留，consumer_session.py:190 仅日志用，无下游语义依赖。
- 保守性核对（investigation_steps）：
  - echo 抑制（:54-55）与 emergency/wake（:56-62）**始终在最前**，短路块不触碰，echo 不可绕过 ✅
  - 短路只对 **RESPOND** 生效（宁可多走 LLM，不错误拒答）；multi_speaker 这个 **RECORD_ONLY** 规则见 §5 BC3 权衡。

## 5. 行为变化边界（flag=true 相对当前；flag=false 无变化）

| 边界 | 输入 | 当前行为 | flag=true 后 | 方向 | 风险 |
|------|------|---------|-------------|------|------|
| BC1 | 活跃对话内任意句 | LLM 先判，可高置信 RECORD_ONLY 否决 | 直接 RESPOND，不调 LLM | 少漏答 / 可能多答 | 低（活跃对话续答几乎总正确，TTL=10s 窗口窄） |
| BC2 | 疑问句（？/什么/吗…，单/已识别说话人，非活跃） | LLM 先判，可高置信 RECORD_ONLY 否决（"与他人交谈"） | 直接 RESPOND，不调 LLM | 少漏答 / **多答人际问答** | **中——本 batch 主风险**：家人互相提问会误触发。符合「漏答比慢答差」的优先级，但与历史调参方向（阈值0.6→0.75、TTL30→10 皆为**减少误触发**）相反 → §7 待确认 |
| BC3 | 未识别说话人 + recent≥2 + 声明句 | LLM 先判，可高置信 RESPOND | multi_speaker 先命中 → RECORD_ONLY，不给 LLM 机会 | **可能漏答** | 中——与「宁可多走 LLM 不错误拒答」冲突。仅在 speaker 未识别时（ambient 经 batch-17 通常已识别，窄）。备选：把 multi_speaker 放到 LLM 之后（见 §7 备选设计 B） |
| BC4 | 普通声明句（非疑问、单/已识别、非活跃） | LLM 判定 | **仍走 LLM**（末位兜底），决策不变、仅顺序变 | 无变化 | 低——LLM 保留对隐式指令（如"把灯关了"）的判定能力，收益不覆盖此类 |
| BC5 | 唤醒词/紧急/echo | 最前命中 | 完全不变 | 无 | 无 |

## 6. 短路规则集（flag=true 生效摘要）

- **短路 RESPOND（跳过 LLM）**：`active_conversation`（is_active_conversation 命中）、`question_detected`（`_check_question_features`：中英问号 / QUESTION_WORDS / 句末 QUESTION_PARTICLES）。
- **保留 LLM 兜底**：ambient 模式下的非唤醒/非活跃/非疑问声明句 → `_classify_intent_llm`，conf≥`VOICE_DECISION_LLM_THRESHOLD`(0.75) 采纳，超时安全降级 RECORD_ONLY（:118-123 不变）。
- **不短路 / 不改**：echo(DISCARD)、emergency(STOP)、wake(RESPOND) 三前置门；multi_speaker(RECORD_ONLY) 位置见 §7 备选。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **scope 缺 test 文件**：behavior-change 回归测试实际在 `backend/tests/voice/test_response_decision_llm.py`（1463 行），04-plan scope 只列了 `test_response_decision.py`。以下 3 个测试**编码了当前「LLM 先于规则」的顺序，flag=true 后必然失败，必须更新**：
  - `test_llm_record_only_high_confidence_skips_rule_engine`（:246，含问号文本断言 LLM RECORD_ONLY 压过规则）→ flag=true 下 question 先短路 RESPOND，断言反转
  - `test_timeout_returns_record_only_even_with_active_conv`（:403，断言活跃对话内 LLM 超时仍 RECORD_ONLY）→ flag=true 下 active_conv 先 RESPOND、LLM 不触发
  - `test_low_confidence_fallthrough_to_active_conv`（:289，断言穿透到 active_conv）→ 结果同为 RESPOND 但 LLM 不再被调用，需按 flag 分别断言
  **请确认把 test_response_decision_llm.py 纳入 batch scope（+1 文件），并采用「flag=false 保留原断言、flag=true 新增 override_settings 断言」的双轨测试策略。**
- [ ] **flag 默认值**：建议 `false`（合并零行为变化，灰度后置 true）。若安琳希望立即拿 SLO 收益可设 `true`——但会立刻引入 BC2 人际问答误触发。请拍板默认值。
- [ ] **BC2 哲学冲突**：历史调参（threshold 0.6→0.75、ACTIVE_CONV_TTL 30→10）方向是**减少 ambient 误触发**，而本 batch 短路 question→RESPOND 会**增加**误触发。batch 指令给的优先级是「漏答比慢答差」（短路只多答不漏答），据此短路 question 合规。但需安琳确认接受「家人互相提问可能触发助手」这一体验代价，或改为只短路 active_conversation、question 仍走 LLM（更保守，收益减半）。
- [ ] **BC3 multi_speaker 位置**：备选设计 B——把 multi_speaker(RECORD_ONLY) 移到 LLM **之后**，保留 LLM 对未识别多说话人指令的应答机会（更贴合「不错误拒答」），代价是多说话人疑问句会 over-answer。默认推荐设计 A（保持 multi_speaker 原相对位置）。请择一。

## 8. 验证计划

### 8.1 自动化（每步）
- [ ] `pytest backend/tests/voice/test_response_decision.py -v`（全绿；新增短路矩阵）
- [ ] `pytest backend/tests/voice/test_response_decision_llm.py -v`（**纳入 scope 后**：flag=false 原断言全绿 + flag=true 新断言全绿）
- [ ] `ruff check backend/apps/voice/services/response_decision_service.py`（不新增错误；既有 3 处 E701 不在本批范围）
- [ ] `pytest backend/tests/voice/ -v`（voice 全量回归）

### 8.2 短路测试矩阵（新增，覆盖 §5 边界）
| 用例 | 输入 | is_active | speaker_identified | recent | flag | 期望 decision | 期望 reason |
|------|------|-----------|-------------------|--------|------|--------------|-------------|
| M1 | "今天天气怎么样？" | False | True | 0 | true | RESPOND | question_detected（**未调 LLM**：mock httpx 断言 not called） |
| M2 | "好的我知道了" | True | True | 0 | true | RESPOND | active_conversation（未调 LLM） |
| M3 | "把客厅灯关了" 声明句 | False | True | 0 | true | 依 LLM mock | llm_*（**LLM 仍被调用**：断言 called once） |
| M4 | "今天吃什么" | False | False | 3 | true | RECORD_ONLY | multi_speaker（设计 A；LLM 不调用） |
| M5 | "这是什么？"（同 M1 文本） | False | True | 0 | false | 依 LLM mock 高置信 RECORD_ONLY | llm_*（**旧行为**：LLM 先判压过 question） |
| M6 | "好的我知道了" | True | True | 0 | false | 依 LLM mock/超时 | 旧行为（LLM 先于 active_conv） |
| M7 | echo 文本 | - | - | - | true | DISCARD | tts_echo_detected（短路不绕过 echo） |
| M8 | "小鱼你好" | False | True | 0 | true | RESPOND | exact_wake_word（前置门不受短路影响） |

### 8.3 手动验证（安琳，机器不可自动化 → 需真机）
- [ ] flag=true：对疑问句触发 ambient，日志**无** `stage=decision.llm_classify`，`decision.decide` duration_ms < 100
- [ ] flag=true：对普通声明句（非疑问）触发，日志**仍有** `decision.llm_classify`（LLM 未被误绕过）
- [ ] flag=true：家人互相提问场景，观察 over-answer 频率（BC2 体验回归）
- [ ] flag=false：行为与优化前一致（回滚开关有效性验证）

### 8.4 性能验证（batch-29 埋点，需真机+Gateway 在线）
- [ ] 对比 flag on/off 的 `decision_llm` hop（consumer_session.py:217，仅 RESPOND 分支记录）
- [ ] 预期：question/active_conv 输入的 decision_llm hop P50 由 ~0.8-2s 降至 < 0.1s
- [ ] Gateway 当前离线（03-hotpath-delta 注）→ 性能数字待压测，代码合并不依赖此项

## 9. 回滚策略

- **首选（运行时，零部署）**：`VOICE_DECISION_SHORTCIRCUIT_ENABLED=false` → decide() 走 else 旧顺序，行为逐字节复原。
- **次选（代码）**：`git revert <commit>`（单 commit，仅 4 文件，无 migration、无 schema、无 API 契约变更）。
- 无 Do Not Touch 跨越，无第三方依赖引入。

## 10. 执行预算

- 预计 tool calls：~25（读 2 test 文件精确定位 + 4 处 Edit + 2 轮 pytest）
- 预计完成：1 session（与 estimated_sessions=1 一致，未超 2×）
- 不建议拆分。**唯一扩张**：scope +1 test 文件（§7 待确认），不改变 session 预算。

## 11. 禁止/边界重申
- 严禁停止/重启服务、严禁清理操作（本 batch 纯代码+测试，验证用 pytest，不碰 services.sh）。
- 严禁改 echo/wake/emergency 三前置门；严禁改 `_classify_intent_llm` 内部逻辑（仅改其被调用的位置）。
- 严禁触碰 SM4/get_active_model 解密路径（禁区第 3 条）。
