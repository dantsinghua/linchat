
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

## batch-09（2026-07-17 全新执行，P1 blocks_slo）
- 无人值守拍板项待安琳 review：单条常驻 TTS 流式会话设计；tts_synth 埋点口径在增量模式下含 LLM 重叠时间（代码注释已标注）。
- 性能收益待真实语音流量验证（预期 TTS 与 LLM 重叠节省 1-2s）。
- 回滚开关：VOICE_TTS_INCREMENTAL_ENABLED=false（回退整体 enqueue 旧路径）。
- barge-in/comfort/shutdown 状态机与流式会话的交互建议真实设备抽查。

## batch-10（2026-07-17 全新执行，P1 blocks_slo）
- 无人值守拍板项待安琳 review：VOICE_TTS_PRECONNECT_ENABLED 默认 off（保守上线，建议观察 1006 消除效果后再开启预连接）；放弃会话级连接池的最小增量方案。
- 待线下佐证：code=1006 根因（关闭顺序反转后 grep 后端日志确认 1000 占比）；预连接收益压测。
- ASR 共用 ws_client_base 的关闭路径已有回归测试守护。

## batch-11（2026-07-17 全新执行，P1）
- 无人值守拍板项待安琳 review：BlockingConnectionPool（超限等待，timeout=10s）+ max_connections=50。
- 运行时观测项：服务重启后 redis-cli info clients 观察 connected_clients 是否下降且稳定（plan §5.2 只读观测）。
- 顺带修复：test_coverage_boost.py 两用例的 get_event_loop 废弃写法（顺序依赖，batch-11 新测试暴露）。

## batch-12（2026-07-17 配额中断后续作完成，P1）
- 无人值守拍板项待安琳 review：model_config 60s TTL 缓存（ORM 直改 ModelConfig 后最长 60s 生效延迟）；明文 SM4 解密 key 进程内存留存 60s（不落日志/盘）——请复核安全性。
- wake_words 同为 60s TTL：新增唤醒词后最长 60s 生效。

## batch-27（SLO 测量 — 2026-07-17 15:00 被外部依赖阻塞）
- ASR/TTS Gateway 离线：frpc visitor 127.0.0.1:8100 本地监听正常，但 STCP 对端 llm-gateway 无 HTTP 响应（health 000）。语音链路第一跳 ASR connect failed，无法触发测量。
- 测量工具已就绪：refactor/loop/trigger_voice_e2e.py（模拟 reSpeaker 推 wav，单次冒烟已验证到 ASR connect 环节，设备认证/trace_id/WS 协议全通）。
- **待安琳**：拉起 GPU 机器上的 llmgateway（或检查 STCP server 侧），恢复后运行：
  `linchat/bin/python refactor/loop/trigger_voice_e2e.py 20 && ./scripts/measure-voice-latency.sh 20`

## batch-14（2026-07-17，核实后缩水执行）
- 4 月 plan 的"9 个 0 调用者 shim"严重过时：仅 chat/sse.py 真正可删（已删）；8 个有调用者转 batch-15 处理；generation.py 被误列为 shim（实为含 register/signal 生产逻辑），已剔除——建议安琳复核 plan JSON 中 batch-15 条目是否需要相应修正。

## batch-15 Part A（2026-07-17）
- Part B 延后：chat/services 剩余 6 shim / 11 文件迁移（batch-15b，rediagnosis 时应入计划）。

## batch-30（2026-07-17，dark-launch）
- **需安琳产品决策**：VOICE_DECISION_SHORTCIRCUIT_ENABLED（默认 false）是否开启——短路疑问句可省 decision_llm 一跳（约数百 ms~1s），但与历史"防人际问答误触发"调参方向相反（BC2 争议，见 batch-30-plan.md §7）。建议先看 batch-29 埋点数据中 decision_llm 实际耗时再定。

## batch-31（2026-07-17，dark-launch）
- **需安琳决策**：VOICE_HA_PARALLEL_TTS_ENABLED（默认 false）是否开启——HA 下发与浏览器 wait_idle 并行可省 1-2s；C1 残留：barge-in 无法截停已发出的小爱 POST（现状限制，长期解为 PD-6 HA 流式接口）。

## batch-32（2026-07-17，dark-launch）
- **需安琳决策**：VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED（默认 false）是否开启——句末标点即时 flush 可省最多 1.5s 聚合等待；风险为 ASR 中途给出标点导致拆句（矩阵已覆盖已知模式）。
- 三个 dark-launch 开关（batch-30 短路 / 31 HA 并行 / 32 自适应 flush）+ batch-10 预连接开关，建议 Gateway 恢复后用 trigger_voice_e2e.py 分别开关对比 latency.summary 数据再定。

## batch-33（2026-07-17，voice service ORM 收敛）
- 手动验证（plan §7.2，未在无人值守中执行）：
  - 触发 ambient 语音持久化：确认 Message.is_voice 标记 + MediaAttachment 音频写入 + record-only 超限清理行为不变。
  - 声纹注册后追溯匹配：未知 speaker 历史消息正确改归属（§3 直连点 7）。
- 无人值守拍板项待安琳复核（plan §9）：
  - scope 扩项：MediaAttachment.objects.create 收敛落在 media/repositories.py，files_touched 5→6 文件（已按分层原则执行）。
  - is_voice `.save()` 边界：已一并收敛为 repo 同步方法 set_voice_flag_sync（保持一致性）。
  - PD-4 默认采纳"收敛 message_repo"方案（与 ambient_light 一致），请正式拍板。
  - voice_pipeline.py 仍 326 行（>300 硬限制），本 batch 不拆分，拆分留待后续 batch。
