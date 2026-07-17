# LinChat 无人值守重构循环 — 最终报告 2026-07-17（续跑 R2）

> 承接前一 run（loop-report-20260717.md，因账号月度配额上限停在 batch-12 半成品）。
> 本 run 从 refactor/batch-33 分支断点续跑，完成 batch-33~36 + Rediagnosis R2。

## 结论：全部自主 batch 完成，计划已收敛；循环因 **性能目标无法自主测量** 停止（阻塞于外部依赖 Gateway 离线）

- **Goal 1（全量测试全绿，零 bug）**：✅ 达成。全量 pytest 1772 passed / 9 skipped / 0 failed（每次 merge 前后 gate 均绿）。
- **Goal 2（voice_e2e_p50_ms 较基线改善 ≥20%）**：⛔ **无法裁定** —— 不是代码慢，而是 **ASR/TTS Gateway 离线导致无实测数据**（`voice_e2e_p50_ms=0`，PERF_TARGET: NOT_MET 因 insufficient data）。优化代码已实现但为 dark-launch（flag 默认 false），悬空于 Gateway 恢复后的实测与灰度转正。

## 本 run 完成的 batch（承接 R1 诊断 batch-29~36）
| batch | 类型 | 内容 | 结果 |
|-------|------|------|------|
| batch-33 | P2 refactor | voice service 8 处直连 ORM 收敛至 message_repo/media repo | ✅ merged，1771 passed |
| batch-34 | P2 refactor | 删除 chat/services 剩余 6 中枢 shim + tokenizer，调用点直连真实模块 | ✅ merged，1771 passed |
| batch-35 | P2 refactor | chat/services/generation.py 迁移到 graph，消除 graph→chat 反向耦合 | ✅ merged，1771 passed |
| batch-36 | P2 fix | 孤儿 embedding 派发前 rowcount gate + worker not-found WARNING→DEBUG，铲除测试污染源 | ✅ merged，1772 passed |

至此 04-refactor-plan.json 中 batch-04~36 全部 completed。

## Rediagnosis R2（本 run 第 1 轮，3 轮上限内）
增量范围：`diag-20260717..HEAD -- backend/`（即 batch-29~36 产物）。三份只读诊断 + refactor-planner 裁定，结论 **零追加、计划收敛**：
- **架构**（`diag-20260717-r2/01`）：R1 头号债（voice 直连 ORM）已由 batch-33 完整清偿，voice/services 现零 `.objects.`；无新增循环依赖/跨层穿透。剩余候选全部落在"需安琳决策/运维激活"象限。
- **性能**（`diag-20260717-r2/03`）：不建议新增 perf batch。R1 三大软件串行等待（决策 LLM / 聚合窗口 / HA-浏览器 TTS 串行）已由 batch-29~32 落地为可灰度代码；决定性两跳（ASR 段末 pad 2s、小爱本地 TTS ~1s）非 backend 可安全消除。瓶颈 = 优化未启用 + 无实测。
- **日志**（`diag-20260717-r2/02`）：0 个 ERROR/CRITICAL/可自主修复 bug。两个 WARNING（embedding id=1 ×11、captcha 404 ×40）均为"进程未重启到最新代码"或客户端源问题。

## ⚠️ 待安琳的遗留项（循环无法自主处理）
1. **【阻塞 Goal 2】拉起 GPU 机器上的 llmgateway（或检查 STCP server 侧）**，恢复 ASR/TTS，然后运行：
   `linchat/bin/python refactor/loop/trigger_voice_e2e.py 20 && ./scripts/measure-voice-latency.sh 20`
   —— 才能测到 voice_e2e_p50_ms 基线并裁定 20% / 5s SLO。
2. **灰度转正 3 个 dark-launch flag**（batch-30 决策 LLM 短路 / batch-31 HA 并行 / batch-32 自适应 flush）+ batch-10 预连接：Gateway 恢复后用 trigger_voice_e2e.py 分别开关对比 latency.summary 数据再定。5s SLO 收益全部悬空于此。
3. **重启后端**激活 batch-36（`./scripts/services.sh restart`），消除 embedding id=1 WARNING 噪声（当前 worker 跑的是旧字节码）。
4. **captcha 404 storm**（632ms 内 40 请求打带尾斜杠 `/api/v1/auth/captcha/`）：根因在客户端（陈旧构建/缓存/探针），后端兜底触碰 URL 契约需你拍板，请先定位调用方。
5. **voice_pipeline.py 326 行 >300 硬限制**：R1/R2 均决定不在活跃优化期拆分，留待专项 batch。
6. **chat/services/__init__ 5 个死 re-export**：确为零代码消费者，但是 CLAUDE.md 文档化的对外契约，废除需你拍板。

## Manual-review backlog
本 run 追加了 batch-33 / batch-36 的手动验证项（语音持久化行为核对、声纹追溯匹配、2h 窗口日志观察）至 `refactor/loop/manual-review-backlog.md`，无人值守未执行，非阻塞。

## 停止原因
Rediagnosis 收敛（零新增 batch）+ validate 全绿，但 **perf 目标因外部依赖（Gateway 离线）无法测量裁定**。这不是循环可自主推进的状态——需安琳先恢复 Gateway。故按协议发提醒告警并停止。
