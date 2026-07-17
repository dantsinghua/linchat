# LinChat 运行时问题诊断（Rediagnosis R2 · 增量）

> 生成时间：2026-07-17 17:54 (GMT+8)
> 数据范围：服务 15:40 声明重启，实际进程 15:54:45 拉起 → 采样至 17:50
> 日志源：/tmp/linchat-{backend,celery-worker,celery-beat,frontend}.log（当前轮转，无历史 .1 文件）
> 关注点：今日 loop 合并（trace_id/埋点/celery signals batch-28/ambient/TTS/Redis 池/settings 拆分）运行时表现
> 只读产出，未修改任何业务代码或日志。

## 执行摘要

- 日志总行数：286（backend 7 / worker 206 / beat 63 / frontend 10）
- ERROR / CRITICAL 总数：**0**；Traceback：**0**；Exception 堆栈：**0**
- WARNING 聚类数：**2**（`Doc embedding attachment not found` ×23；`django.request Unauthorized /` ×1）
- **新问题数：0**（今日 loop 改动未在运行时引入任何 ERROR/WARNING/traceback）
- 最严重项：`media/tasks.py:56` "attachment not found id=1" ×23（既有逻辑，LOW，非本次 loop 引入）
- trace_id 贯穿率：**100%**（HTTP 与 Celery 任务日志均带 32-hex trace_id；见 §5）
- JSONFormatter：**正常**，无格式化错误
- 首 token / 慢请求：**无数据**（重启后无 LLM/SSE 业务流量，见 §10）

## 1. 日志清单

| 文件 | 大小 | 行数 | 时间范围 | 说明 |
|------|------|------|---------|------|
| /tmp/linchat-backend.log | 1.7K | 7 | 15:54:45–15:54:54 | 仅启动 + 1 次 `GET / 401`，无业务流量 |
| /tmp/linchat-celery-worker.log | 29K | 206 | 15:54:46–17:50:27 | 主体日志，周期任务全部 succeeded |
| /tmp/linchat-celery-beat.log | 7.1K | 63 | 15:54:47–17:50:27 | 调度正常，无异常 |
| /tmp/linchat-frontend.log | 274B | 10 | 15:54 | Next.js 启动，1 条 standalone 配置 warn |
| 历史轮转 (.log.1) | — | — | — | 不存在（首次轮转，无历史窗口） |

## 2. 错误模式聚类

| 级别 | 模式 | 频次 | 代表代码位置 | 事实 / 推测 |
|------|------|------|-------------|------------|
| WARNING | Doc embedding: attachment not found id=1 | 23 | `backend/apps/media/tasks.py:56` | 事实：generate_document_embeddings 收到 id=1 但 DB 无此行 |
| WARNING | Unauthorized: / (401) | 1 | `django.request`（框架层） | 事实：未登录访问根路径，健康探针类，正常 |
| INFO | Embedding health check: retried=0, ... total_failed=0 | 2 | `backend/apps/memory/tasks.py:111` | 事实：embedding 队列 0 积压，健康 |

无 ERROR/CRITICAL/Traceback 可聚类。

## 3. LLM / Agent 错误专项

**无数据。** 重启后 backend 仅 7 行日志，无任何 LLM 调用、SubAgent 工具调用、LangGraph/LangChain、Langfuse trace 记录。
`LLMConnectionError/LLMTimeoutError/LLMRateLimitError/LLMContentFilterError` 四类异常本窗口内 **0 次**。
（14:38 的 "ASR connect failed"（Gateway 离线）发生在 15:54 重启之前，不在当前日志窗口内，team-lead 已标注为已知项，此处不计。）

## 4. 慢请求分析

**无数据。** 当前日志无 `duration_ms/cost/elapsed/latency` HTTP 埋点记录（无业务请求）。
Celery 任务耗时可见且全部健康：
- `retry_failed_*` / `expire_guests` / `embedding_health_check`：0.01–0.06s
- `generate_document_embeddings`：0.010–0.023s（均走 "not found" 早返回路径）
无超指标项（因无 LLM 首 token 场景可测）。

## 5. 可观测性缺口（重点验证 loop 改动）

**trace_id 贯穿：验证通过，贯穿率 100%。**
- HTTP 链路：backend.log 第 6–7 行同一请求的 `django.request` 与 `uvicorn.access` 共享
  `trace_id=bf9e50b7b67044ee8468fa942a635d5f` —— 中间件埋点生效。
- Celery 链路：worker 内 25 个带应用日志的任务各自携带 32-hex trace_id（如 `410057ad...`、`70f3b95a...`）。
- **batch-28 signals 验证通过**：`core/celery.py:69/81/93`（before_task_publish / task_prerun / task_postrun）
  重启后运行 2 小时，周期任务全部正常 receive→succeed，无 signal 异常、无 `_trace_tokens` 泄漏告警；
  beat 无上下文任务由 `task_prerun` 的 `uuid.uuid4().hex` 兜底（`core/celery.py:87`），日志中 trace_id 均非 "-"。
- **JSONFormatter 验证通过**：`core/logging_config.py:18` 输出的 JSON 全部合法，字段
  `time/level/logger/trace_id/msg/module/lineno/taskName` 齐全，无格式化异常、无 `KeyError`。

**缺口（既有，非新增）**：backend HTTP 访问日志无 `duration_ms` 字段，无法从日志侧统计 API p95；
依赖 Langfuse 侧观测 LLM 延迟。属可观测性待补，非本次 loop 回归。

## 6. 沉默失败

本窗口 **无被吞异常触发**。代码侧既有的 broad-except 沉默点（仅静态观察，本窗口未触发）：
- `backend/apps/media/services/document_cache.py:71-72` — embedding dispatch 失败 `except Exception` → WARNING + 继续（设计为非阻塞，合理）。
- `backend/core/celery.py:77/89/100` — 三个 signal 的 `except Exception: pass`（注释明确：signal 绝不能打断任务发布/执行）。设计合理，但若 trace_id 注入静默失败将无告警——本窗口未发生。

## 7. 资源告警

- 连接池 / 超时 / pool exhausted：**0 命中**（含今日新增 Redis 池改动，无耗尽/超时日志）。
- PostgreSQL / Redis 慢查询：**0 命中**。
- WebSocket 异常断开（语音/TTS 流式）：**无数据**（无 WS 流量）。
- Celery 任务堆积：**无**。beat 每 5 分钟调度，worker 即时消费，队列 0 积压；`embedding_health_check` 连续 2 次 `stuck_pending=0, stuck_processing=0`。

## 8. 综合问题清单（按优先级）

| # | 问题 | 频次 | 影响范围 | 对应代码 | 优先级 | 是否本次 loop 引入 |
|---|------|------|---------|---------|--------|-----------------|
| 1 | generate_document_embeddings 收到孤儿 id=1（DB 无此行） | 23 | 仅日志噪声，任务已 guard 早返回，无副作用 | `apps/media/tasks.py:56`；派发源 `apps/media/services/document_cache.py:69` | LOW | 否（既有逻辑） |
| 2 | frontend `next start` 与 `output: standalone` 配置不匹配告警 | 1 | 启动告警，服务仍 Ready；建议改用 `node .next/standalone/server.js` | frontend `next.config` output 配置 | LOW | 否 |
| 3 | backend HTTP 无 duration 埋点，日志侧无法算 API p95 | — | 可观测性缺口 | 中间件/访问日志 | INFO | 否 |

## 9. Open Questions

1. **Q1（问题#1 根因）**：`generate_document_embeddings.delay(attachment_id=1)` 反复被派发，但 worker 执行时
   `MediaAttachment(attachment_id=1)` 不存在。派发路径 `document_cache.py:69` 要求先持有 attachment 对象才会 dispatch——
   为何随后行消失？推测：(a) E2E/探针反复解析一个未持久化(事务未提交/回滚)的 attachment_id=1；或 (b) 上传后 attachment 被清理任务删除但 embedding 任务已入队产生竞态。**需确认是否有测试/探针在打 id=1，或存在删除-派发竞态。** 非阻塞但建议派发前校验存在性或降为 DEBUG。
2. **Q2（数据窗口）**：重启后 backend 无业务流量，本次无法验证今日 loop 的 LLM 分类异常处理、ambient 轻量路径、TTS 流式、SSE 心跳在真实请求下的运行时表现。**建议跑一轮 E2E（登录+对话+文档+语音）后再采样一次**，以覆盖 §3/§4 空白。

## 10. 数据限制说明

- **无历史轮转文件**：`/tmp/*.log.1` 不存在，只能看到 15:54 重启后 ~2h 窗口，无法做跨天/突发对比。
- **backend 业务流量近乎为零**（7 行）：LLM/Agent 错误(§3)、慢请求(§4)、WS 断开(§7) 均无数据，非"无问题"而是"无样本"。结论"0 新问题"仅对 Celery/埋点/日志格式/signals 链路成立，业务链路需补跑 E2E 验证。
- journalctl --user 未纳入（服务由 services.sh 管理，日志已落 /tmp，非 systemd 托管）。
