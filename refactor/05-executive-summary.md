# LinChat 重构方案执行摘要（Phase 1 产出）

> 生成时间：2026-04-16T17:30+08:00
> 生成者：refactor-planner-v2
> 输入：legacy-and-debts.md + 01-architecture-map.md + 02-issue-diagnosis.md + 03-call-chain-analysis.md

---

## 项目健康度

- **总体**：黄灯
- **评分（1-10）**：5.5
- **主要扣分项**：
  - 声纹识别 100% 失败（017 核心功能失效）：-1.5
  - 端到端语音延迟 P50=10.8s（SLO 5s 全部超标）：-1
  - 13 个测试失败（CI 红色）：-0.5
  - Trace ID 完全缺失（跨服务排障盲区）：-0.5
  - except Exception 143 处吞异常：-0.5
  - 12 个兼容 shim 残留 + 死文件：-0.5

---

## Top 3 优先事项

1. **声纹识别 bug**（batch-01）— 017 特性核心功能 100% 失效，219/219 次 identified=False，所有消息显示为 dantsinghua。P0 Day-1 必修。
2. **13 个失败测试**（batch-02, 03）— CI 红色，阻塞后续所有重构的回归验证。根因明确：数据库残留 + 断言过期。
3. **端到端语音延迟**（batch-07~10, 27）— 安琳核心痛点，目标 5s SLO，当前 P50=10.8s。规划 4 个优化 batch + 1 个验证 batch 攻坚。

---

## 执行路线图

| 阶段 | Batches | 预估 sessions | 里程碑 |
|------|---------|--------------|--------|
| **P0 Day-1 fix** | batch-01 ~ 03 | 3 | CI 绿色基线 + 声纹 bug 修复 |
| **P0 可观测性** | batch-04 ~ 07 | 4 | trace_id 贯穿全链路 + 语音延迟基线数据 |
| **P1 语音 SLO** | batch-08 ~ 10, 27 | 6 | 端到端 P50 < 5s |
| **P1 其他性能** | batch-11 ~ 12 | 2 | Redis 连接池 + PromptBuilder 并行化 |
| **P2 死代码/shim** | batch-13 ~ 16 | 4 | 12 shim 清理 + 死文件删除 + types 迁移 |
| **P2 settings 拆分** | batch-17 ~ 18 | 2 | 513 行 settings.py 拆为 7 个域文件 |
| **P2 voice Mixin** | batch-19 ~ 21 | 3 | Protocol 约束 + consumer 覆盖率提升 |
| **P2 except 缩减** | batch-22 ~ 23 | 2 | 143 处缩减到 < 60 处 |
| **P2 文档清理** | batch-24 | 1 | DocumentParseService 职责单一化 |
| **P3 测试补全** | batch-25 ~ 26 | 2 | 低覆盖模块 > 80% + 总体覆盖率 > 80% |

**总计：27 batch / 28 sessions / 约 5-7 周**（每日 1-2 个 batch）

---

## 建议起步顺序

```
Day 1:   batch-01  声纹 bug 修复（需先查 SpeakerProfile 表确认 H1/H2/H3）
Day 2:   batch-02  修复 9 个数据库隔离类失败测试
Day 3:   batch-03  修复 1 个断言过期测试 → 里程碑：CI 全绿
Day 4:   batch-04  trace_id 中间件基础设施
Day 5:   batch-05 + batch-06（可并行）trace_id 接入 chat + voice
Day 6:   batch-07  端到端语音精细埋点 → 里程碑：延迟可见
Day 7-8: batch-08  ambient 轻量 prompt（最大收益点，预计 -4.5s）
Day 9:   batch-09  Agent→TTS 流式转发（预计 -1.5s）
Day 10:  batch-10  TTS WS 连接复用（预计 -1s）
Day 11:  batch-27  5s SLO 达成验证 → 里程碑：语音延迟达标
Day 12+: P2 技术债按需推进
```

---

## 5s SLO 攻坚方案

**实测延迟（2026-04-16，19 个 Pipeline 样本）**：

| 指标 | Pipeline→HA 播报 | 含聚合+ASR（估算） |
|------|------------------|-------------------|
| P25 | 7.1s | ~12s |
| **P50** | **10.4s** | **~15s** |
| P75 | 17.2s | ~22s |
| 最快 | 6.7s | ~11s |

| 当前瓶颈 | 量级 | 优化 batch | 预期削减 |
|----------|------|-----------|---------|
| LLM 走完整 Agent（含 SubAgent 路由） | P50=~6s | batch-08 简化 ambient LangGraph + tools 替代 SubAgent | -3~4s |
| TTS 等全部 token 才合成 | ~2-4s 浪费 | batch-09 流式转发 | -1~2s |
| TTS WS 每次新建连接 | ~1s | batch-10 连接复用 | -1s |
| 聚合等待（UtteranceAggregator 3s） | 3s 固定 | 可调参但需平衡准确性 | -1~2s |
| **合计优化后预估** | | | **Pipeline P50 ~4s** |

---

## 主要风险

1. **voice 模块改动风险最高** — 2794 LOC / 3-Mixin / 7 轮迭代，涉及 voice 的 batch 全部标注 risk:high，每批必带回归测试
2. **声纹 bug 根因不确定** — H1（样本库不足）/H2（阈值偏高）/H3（前端映射错误）需实际调试定位，可能不止一个根因
3. **端到端 5s SLO** — ambient 简化 LangGraph + tools 替代 SubAgent（安琳已确认），TTS 去掉安慰语音但需兜底
4. **types.py 迁移影响面广** — 9 个调用方跨 4 个 app，需仔细验证 import 路径

---

## 安琳决策记录（已全部回答）

- **PD-1 ambient pipeline**：✅ 不能简单直调 LLM，需保留 tools/SubAgent 能力（开灯等操作），但可简化 LangGraph 流程和提示词、SubAgent 直接替换为 tools
- **PD-2 TTS 安慰语音**：✅ 可以不保留安慰语音机制，但超长延迟需要有兜底提示
- **PD-3 Redis 连接池**：✅ 全局改造（不仅限 voice）
- **PD-4 chat↔graph 解耦**：✅ **方案 B** — 把 Message/LangGraphExecution 模型抽到 `core/models.py`，chat 和 graph 都依赖 core
- **PD-5 voice Mixin 重构**：✅ **方案 B** — 改组合模式，Mixin 改为独立 Service 类（session_manager / event_handler / inference_runner），状态封装在各自内部
- **PD-voice_persist ORM**：由 PD-4 决定 — Message 迁到 core 后，voice_persist 通过 core.repositories 访问
- **PD-Redis Channels DB3**：技术调研项，batch-11 实施时验证（aioredis 连接池兼容性）

---

## 明确不做的事（重构禁区 8 条）

1. PostgreSQL schema migration
2. SSE 事件格式变更
3. SM3/SM4 加密方案调整
4. 引入 conversation_id / session_id
5. LangGraph / LangChain 版本升级
6. 前端技术栈迁移
7. Docker 服务拓扑调整
8. Gateway API 契约变更

---

## 下一步

1. 安琳 review 本摘要和 `refactor/04-refactor-plan.json`
2. 回答 5 个 Pending Decisions（尤其 PD-1/PD-2 阻塞 P1 核心优化）
3. 执行 batch-01 开始声纹 bug 修复

---

*详情见 refactor/04-refactor-plan.json（27 个 batch 完整定义）*
