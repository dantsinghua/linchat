
## batch-04（合并于 2026-07-17，复用 April 分支工作）
- April Phase 2c 手动验证已执行：11 项端到端 10 PASS / 1 WARN（M3.c 为框架行为误报，M5 已证核心语义），详见 refactor/batches/batch-04-validation.md 第 5 节。
- 遗留人工确认项：生产环境 uvicorn 启动方式下访问日志是否真正 JSON 化（initializer plan 风险①；本地已验，生产 services.sh 启动路径未复核）。

## batch-05（合并于 2026-07-17，复用 April 分支工作）
- April Phase 2c 运行时 E2E 已执行：8/8 PASS（scripts/validate-batch-05.sh），trace_id 贯穿 chat/graph 链路。
- 遗留人工确认项：生产流量下 SSE 对话日志的 trace_id 贯穿性抽查（本地 E2E 已验，生产未复核）。
