# 重构计划增补（Rediagnosis R3 · 2026-07-17）

> 输入：diag-20260717/01-architecture-delta.md、02-issue-diagnosis.md、03-hotpath-delta.md
> 基线计划：refactor/04-refactor-plan.json（batch-01~28 除 27 外全 completed；27 blocked_gate 等 Gateway）
> 本次动作：向 batches 数组 APPEND batch-29~36（8 个）。global_constraints 不变。

## 一、新增批次一览（8 个）

| id | 标题 | 优先级 | 类型 | depends_on | risk | 溯源 |
|----|------|--------|------|-----------|------|------|
| batch-29 | 补齐语音延迟埋点三缺跳（聚合等待/speaker identify/决策LLM） | P1 | observability | batch-07 | high | 03-hotpath-delta §2.5 hop_sum 系统漏计 3-4s |
| batch-30 | 决策 LLM 意图分类移出关键路径（高置信短路） | P1 | performance | batch-29 | high | 03-hotpath-delta §5 R1（5s SLO 首要障碍 +0.8~2s） |
| batch-31 | 小爱 HA 下发与浏览器 TTS wait_idle 解耦 | P1 | performance | batch-29 | high | 03-hotpath-delta §5 R2（小爱串行浪费，batch-09/10 零收益） |
| batch-32 | 聚合窗口自适应即时 flush（句末标点） | P1 | performance | batch-29 | high | 03-hotpath-delta §3 必砍项3（省 0.5-1.5s） |
| batch-33 | voice service 直连 ORM 统一收敛 message_repo | P2 | tech-debt | — | high | 01-architecture-delta §2（8 处 ORM 违规不一致） |
| batch-34 | chat/services 剩余 6 中枢 shim + tokenizer 清理（batch-15 Part B） | P2 | tech-debt | batch-15 | medium | backlog 继承（batch-15-plan §4 Part B） |
| batch-35 | chat/services/generation.py 迁移到 graph | P2 | tech-debt | batch-16 | medium | backlog 继承（batch-16 延后决策） |
| batch-36 | 消除孤儿 embedding 派发日志噪声（id=1 ×23） | P2 | tech-debt | — | low | 02-issue-diagnosis §8 问题1 / §9 Q1 |

### 设计要点

- **先埋点后优化**：batch-29（补齐 3 缺跳埋点）作为 batch-30/31/32 的前置依赖。当前 hops 系统性漏计 latency_start 之前的 ~3-4s（聚合 1.5s + speaker identify + 决策 LLM），不补齐则 5s SLO 归因失真、优化收益无法量化。
- **P1 仅收 5s SLO 真障碍**：R1（决策 LLM 串行）、R2（小爱串行浪费）、聚合固定 1.5s——三者是 03-hotpath-delta 静态下限（5.5-7s）里可砍的固定/串行项。batch-08/10 对小爱路径零收益，故 R2 单列。
- **每个 P1 batch 的 validation 含"用 batch-29 埋点对比优化前后该跳耗时"**，可量化验证。
- **voice 全部 batch（29-33）标 risk: high 且带回归测试**（voice 为最高风险子系统）。
- **batch-31 只做可达部分**（顺序解耦，不触 Gateway/HA 契约）；小爱真正增量流式依赖 HA 能力确认，登记为 **PD-6** 延后，不阻塞本 batch。
- **batch-33 默认采纳"收敛 message_repo"**（与 ambient_light_service 已建立的模式一致），落地前需安琳对 **PD-4** 拍板。
- **每批 ≤12 文件、单 commit 可回滚、依赖显式**，无跨 Do Not Touch。

### phased_rollout 新增分组

- `phase_p1_voice_slo_round2`: [batch-29, 30, 31, 32]
- `phase_p2_layering_cleanup`: [batch-33, 36]
- batch-34/35 并入 `phase_p2_deadcode_shims`

### pending_decisions 新增

- **PD-6**：HA xiaomi_miot 是否有流式文本接口对接 feed_text（决定 batch-31 能否彻底删除小爱串行阻塞）。

## 二、不做清单（够不上成批 / 属禁区 / 无证据）

| 项 | 来源 | 不做理由 |
|----|------|---------|
| settings/__init__.py:8-10 docstring 漂移修正 | 01-arch-delta §1.2 | 纯注释、3 行、无运行时影响。可在下次触及 settings 的 batch 顺手改，不单独成批 |
| voice_pipeline.py(309)/consumer_session.py(303) 按行数硬拆 | 01-arch-delta §4；backlog | 仅超 300 行 3-9 行，voice 最高风险 + 高 churn（pipeline 近 3 月 6 commits），纯行数拆分收益极低而回归风险高。其 ORM 债/小爱串行债已由 batch-33/31 各自处理 |
| settings __init__(~230 行) 再拆 | backlog | base 230 行含 Django 基础配置属合理体量，域拆分已由 batch-17/18 完成，无进一步拆分价值 |
| R3 BlockingConnectionPool 池耗尽静默阻塞 ≤10s | 03-hotpath-delta §4.1/§5 | 家庭低并发概率极低，max_connections=50 覆盖 pubsub 峰值，无实测证据。留观测：batch-29 埋点后若发现 redis 尾延迟再评估 |
| R4 model_config 缓存跨线程竞态 + SM4 明文 key 驻留 60s | 03-hotpath-delta §4.2/§5 | 良性竞态（最坏重复查库一次）；明文 key 驻留属 batch-12 已知设计，触碰 SM4 面为禁区第 3 条 |
| R5 ORM 直改 ModelConfig 绕过缓存失效 60s 陈旧 | 03-hotpath-delta §4.2/§5 | 仅换模型/换 key 运维场景短暂陈旧，家庭低频；加手动 flush 入口收益边际。换模型频繁再评估加 management command |
| voice→chat 三层穿透（ambient_light 直 import chat 模型/仓储/类型） | 01-arch-delta §3 | 属架构耦合权衡，需安琳决策是否经 chat service 门面隔离（Open Question），非明确债务，不强做 |
| 函数内 lazy import 规避循环依赖 | 01-arch-delta §3 | 有意保留作为架构约束标记，能跑；根治需提取 core_models（禁区第 1 条），已在 excluded_items |
| refactor/loop/*.py 工装债务 | 01-arch-delta §5 | 工装独立于 backend、无架构侵入、无实质债务，归低优先级不成批 |
| frontend next start 与 output:standalone 告警 | 02-issue-diagnosis §8 问题2 | LOW 启动告警、服务仍 Ready；前端技术栈不动（禁区第 6 条） |
| backend HTTP 无 duration_ms 埋点 | 02-issue-diagnosis §5/§8 问题3 | INFO 级可观测性缺口，依赖 Langfuse 侧观测 LLM 延迟；无紧迫证据，暂不成批 |

## 三、验证说明

- 环境无 Bash 工具，未能运行 `python3 -c "import json; json.load(...)"` 自动校验；已通过 Grep 结构核对：batches 数组含 37 个 `"id": "batch-"`（原 29 + 新 8），8 个 `"actual_status": "planned"`（batch-29~36）。JSON 按既有条目字段结构严格对齐手工构造。建议安琳在有 shell 的环境补跑一次 `python3 -c "import json; json.load(open('refactor/04-refactor-plan.json'))"` 终验。
