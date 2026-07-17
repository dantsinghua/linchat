
## batch-04（合并于 2026-07-17，复用 April 分支工作）
- April Phase 2c 手动验证已执行：11 项端到端 10 PASS / 1 WARN（M3.c 为框架行为误报，M5 已证核心语义），详见 refactor/batches/batch-04-validation.md 第 5 节。
- 遗留人工确认项：生产环境 uvicorn 启动方式下访问日志是否真正 JSON 化（initializer plan 风险①；本地已验，生产 services.sh 启动路径未复核）。

## batch-05（合并于 2026-07-17，复用 April 分支工作）
- April Phase 2c 运行时 E2E 已执行：8/8 PASS（scripts/validate-batch-05.sh），trace_id 贯穿 chat/graph 链路。
- 遗留人工确认项：生产流量下 SSE 对话日志的 trace_id 贯穿性抽查（本地 E2E 已验，生产未复核）。

## batch-06（合并于 2026-07-17，复用 April 分支工作；April 未做 Phase 2c，全部 manual 项待人工）
- 通过 reSpeaker 说话触发完整语音链路，检查日志中 trace_id 从 ASR 到 HA 播报全程一致

## batch-07（executor 执行于 2026-07-17，端到端语音延迟精细埋点 P0）
- **[新文件 review]** 无人值守循环拍板新增 backend/apps/voice/services/voice_latency.py（收集器 registry，04-plan scope.new_files=[]），
  理由：内聚小模块优于塞进 voice_pipeline.py；请安琳事后确认落点是否接受。
- **[口径确认]** 汇总行双 total：total_from_vad_ms（含聚合1.5s，对齐 5s SLO）/ total_from_speech_end_ms；请确认 SLO 基线取 total_from_vad_ms 的 p50。
- **[ambient 近似归因]** ambient 聚合模式 pipeline segment_id 与上游 ASR 段可能不一致，asr/vad/speech_end 为近似（asr_approx）；push-to-talk 精确。可接受？
- **[手动性能基线待跑]** plan 5.3 基线需 live Gateway/HA：触发 ≥10 次完整语音链路后 grep '"stage": "latency.summary"' /tmp/linchat-backend.log，
  聚合 total_from_vad_ms 的 P50/P95 写入 refactor/baselines/batch-07-voice-latency.json（预期 P50 ~10.8s）。
- **[perf_bench 对齐待办]** scripts/measure-voice-latency.sh 自 batch-06 起已失效（解析旧字符串），本 batch 未修（决策4）；
  loop 需改 measure 脚本为解析 latency.summary 单行（样例行见 batch-07-progress.txt），勿改 perf_bench.sh。

## batch-07（2026-07-17 全新执行）
- 无人值守拍板项待安琳 review：① 新增 backend/apps/voice/services/voice_latency.py（plan scope new_files 原为空，为避免 voice_pipeline.py 膨胀而批准）；② total 双口径（total_from_vad_ms 对齐 5s SLO / total_from_speech_end_ms）。
- scripts/measure-voice-latency.sh 自 batch-06 起解析失效，本 batch 未修；建议改为 grep latency.summary + jq 提取（新格式样例见 refactor/batches/batch-07-progress.txt）。
- 真实语音会话下 latency.summary 汇总行落盘验证（hops 完整性 + delta_pct<0.05）尚未在生产路径抽查。

## batch-28（2026-07-17 全新执行）
- 运行时验证待人工/延后：celery worker 重启后触发 daily_summary，grep 日志确认 trace_id 与发起者一致；beat 周期任务 trace_id 为 32hex UUID；signal 在 threads/gevent 池下的 _trace_tokens 行为复核（当前 prefork）。
- celery beat/worker 未重启，新 signal 代码需下次服务重启后才生效。

## batch-08（2026-07-17 全新执行，P1 blocks_slo）
- 无人值守拍板项待安琳 review：D1 新建 ambient_light_service.py；D3 ambient 轻量路径不做记忆召回（按 plan 条目原意）。
- 性能主指标待真实语音流量验证：ambient 会话 LLM 推理 P50 预期 6.3s→1.5-2s；latency.summary 数据积累后跑 perf_bench.sh 对比 baseline 10828ms。
- 回滚开关：VOICE_AMBIENT_LIGHT_ENABLED=false（settings.py）。
- 环境事故记录：batch-08 executor 运行期间全服务栈被停 + frontend/.next 被删（归因未明，已恢复），后续 executor 提示词已需加强"禁止停服/清理"约束。
