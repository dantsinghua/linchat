# 重构计划增补（Rediagnosis R2 · 2026-07-17）

> 输入：diag-20260717-r2/01-architecture-delta.md、02-issue-diagnosis.md、03-hotpath-delta.md
> 对照：refactor/04-refactor-plan.json（batch-04~36 全部 completed）、diag-20260717/05-addendum.md（R1 addendum，§二"voice_pipeline 不拆"决定）
> 本次动作：**向 04-refactor-plan.json 追加 0 个 batch**。global_constraints 不变。

## 一、裁定结论：零新增 batch，重构计划已收敛

三份 R2 增量报告一致指向"无可自主执行的净收益 batch"。逐项裁定如下（全部**不追加**）：

| 候选 | 来源 | 类型 | 裁定 | 理由 |
|------|------|------|------|------|
| voice_pipeline.py 326 行拆分 | 01-arch §1.2/§4 #1 | P2 tech-debt | **不追加** | R1 addendum §二已决定不拆；voice 为最活跃/最高风险区（近 3 月 6 commits），纯行数拆分收益极低、回归风险高。R2 未提供推翻理由，本轮维持不拆。留待专项。 |
| chat/services/__init__ 5 个"死" re-export 清理 | 01-arch §2.1(b) | P3 tech-debt | **不追加** | 见 §三详述：虽核实无代码消费者，但属**已被 batch-15/34/35 明确决定保留的对外契约**，且 CLAUDE.md 声明为公共 API，废除需安琳单独拍板（batch-34-plan §127 原文"属另一批次"）。 |
| 3 个 dark-launch flag 转正 + 死路径清理 | 01-arch §2.2；03-hotpath §5 | P3 配置 backlog | **不追加** | 触运行时/语音关键路径行为，需安琳按压测结果定转正时点。属"待压测转正"backlog，非可静态修的架构债。 |
| 新增 perf batch（ASR pad / speaker identify 等） | 03-hotpath §2/§4/§5 | P1 perf | **不追加** | 当前瓶颈是"优化未启用（flag 默认 false）+ 无实测"，非缺代码。batch-30/31/32 已就绪待灰度。ASR pad 调参带截断/准确率风险，须 Gateway 恢复后实测，禁止盲目立项。 |
| captcha 404 retry storm 兜底 | 02-issue §2/§8 #1/§9 Q1 | MEDIUM | **不追加** | 根因在客户端（陈旧构建/缓存标签/外部探针，非当前前端源码）。后端兜底需让路由容忍尾斜杠 → 触碰 URL 契约，须先问安琳。非后端代码缺陷。 |
| 孤儿 embedding `id=1` WARNING ×11 | 02-issue §2/§8 #2/§9 Q2 | LOW | **不追加** | 代码已由 batch-36 修复（`logger.debug` + rowcount gate）。运行进程是 batch-36 提交前拉起的旧字节码，`./scripts/services.sh restart` 即消除。属运维激活，非 bug。 |
| frontend standalone 配置告警 / HTTP 无 duration 埋点 | 02-issue §8 #3/#4 | LOW/INFO | **不追加** | R1 已登记既有项；前端技术栈禁区 + 依赖 Langfuse 侧观测，无紧迫证据。 |

**核心判断**：batch-29~36 收敛质量高——R1 头号债（voice services 直连 ORM）已由 batch-33 完整清偿，voice/services 现零 `.objects.`；无新增循环依赖、无新增跨层穿透。剩余候选全部落在"需安琳产品/契约/压测决策"或"运维激活"象限，**无一满足"可自主执行 + 不触契约/禁区 + 明确净收益"的追加门槛**。为延续循环而勉强造 batch违反小步快跑与净收益原则，故**本轮零追加，计划收敛**。

## 二、chat/services/__init__ 死 re-export：核实过程与不追加理由（详）

任务要求"死 re-export 清理若追加，必须先核实零消费者"。已核实：

- **代码消费者**：`rg "from apps\.chat\.services import"` 全库命中的符号均为**载荷型**（ChatService/HistoryService/StreamChunk/MessageVO/InferenceTask/_active_generations/get_stop_event/register|unregister_generation/_get_tool_model_name），**无一处**从 `apps.chat.services` 导入这 5 个兼容符号；真实消费者从各自真源导入（media/views.py ← `apps.media.services`；graph/subagents ← `apps.graph.services`）。→ "零代码消费者"属实。
- **frontend 消费者**：这 5 个是 Python 后端符号，前端（Next.js/TS）本质无法 import → 天然零。
- **但**：`chat/services/CLAUDE.md:95` 明确声明 `__init__.py` 导出"所有公共 API（含兼容层），保持 `from apps.chat.services import X` 可用"——即这是**文档化的公共 import 契约**；且 batch-15/34/35 三次**刻意保留** `__all__` 与中枢 re-export（batch-34-plan §68/§127 原文："`__all__` 不动"、"若你希望废除中枢 re-export，属另一批次，默认不动"）。

**结论**：删除动作虽技术上"无消费者"，却会**单方面推翻一项已文档化、被多批次刻意保留的对外契约决定**，收益仅 5 行"误导性 API 表面"清理，性价比与风险边界不符自主门槛。**归入待安琳，不作 R2 自主 batch。**

## 三、待安琳清单（非 batch，需产品/契约/压测/运维决策）

1. **[perf 前置] Gateway 恢复后测 `voice_e2e_p50_ms` 基线**：batch-29 埋点（含 `delta_vad_pct`/`flush_reason`/`hops`）已就绪。须先测基线确认 ASR pad(2s) 是否真为下一瓶颈，再决定是否立 ASR 调参专项。避免盲目优化。（承 R1，Gateway 离线阻塞）
2. **[flag 转正] batch-30/31/32（+ 长期未转正的 TTS_PRECONNECT）4 个 dark-launch flag 的 A/B 灰度与转正时点**：当前默认 false，5s SLO 收益全部悬空。建议 Gateway 恢复后按 30→32→31 顺序灰度，用 batch-29 埋点观测；转正（置 true / 删旧回退路径）须安琳按压测数据拍板。（承 R1 Q3）
3. **[客户端定位] captcha 404 retry storm（40 req/632ms）调用来源**：确认是陈旧前端构建/浏览器缓存标签、还是外部扫描探针。若属前端 → 前端加退避 + 刷新构建；若要后端路由容忍尾斜杠 → 触碰 URL 契约，须安琳批准。
4. **[voice 专项] voice_pipeline.py(326)/consumer_session.py(310) 拆分**：R1 已决定不拆（高风险活跃区）。留待 voice 稳定一轮后的专项重构，非常规增量批次。
5. **[契约决策] chat/services/__init__ 5 个死兼容 re-export 是否废除**：技术上零消费者，但属文档化公共 API + 多批次刻意保留契约。若安琳同意废除，可单独立一"中枢 re-export 精简"P3 batch（删 5 行 re-export + 收缩 `__all__` + 同步 CLAUDE.md + 全量 pytest 护栏）。默认不动。
6. **[运维激活] 重启激活 batch-36**：`./scripts/services.sh restart` 使 celery worker/backend 加载最新字节码，消除孤儿 embedding WARNING 噪声并使 rowcount gate 生效。
7. **[承 R1 未决] PD-6**：HA xiaomi_miot 是否有流式文本接口对接 batch-09 `feed_text`（决定能否彻底删小爱串行阻塞）。需 HA 能力确认，非 backend 静态可判。

## 四、验证说明

- 未运行 pytest / json.load（本轮无代码改动，无需）。
- 04-refactor-plan.json **未被修改**（零追加）；batches 数组维持 batch-01~36，无新增 id。
- 死 re-export 核实经 Grep 完成：全库 `from apps.chat.services import` 无该 5 符号消费者（详见 §二）。
