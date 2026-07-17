# LinChat 运行时问题诊断（Rediagnosis R2 · 增量 · 零 bug 猎场）

> 生成时间：2026-07-17 22:0x (GMT+8)
> 数据窗口：服务 **20:58:45/46** 拉起 → backend 采样至 **22:00:53**，worker/beat 至 **22:00:27**
> 日志源：/tmp/linchat-{backend,celery-worker,celery-beat,frontend}.log（当前轮转，无历史 .1）
> 关注点：batch-36（孤儿 embedding 降噪）运行时是否生效；任何 ERROR/Traceback 聚类；语音/Celery/沉默失败
> 只读产出，未修改任何 backend/ 业务代码或日志。

## 执行摘要

- 日志总行数：**265**（backend 85 / worker 126 / beat 41 / frontend 11 + 各表头）
- ERROR / CRITICAL / Traceback / Exception：**0**（四文件全 0，grep 确认）
- WARNING 聚类数：**2**
  1. `Not Found: /api/v1/auth/captcha/` 404 **×40**（backend，**本轮新出现**，retry storm）
  2. `Doc embedding: attachment not found id=1` **×11**（worker，既有噪声，见下）
- **可自主修复的代码 bug：0**。两个 WARNING 聚类均非代码缺陷：
  - #1（embedding 噪声）：**batch-36 已在代码树修复，但运行进程是 batch-36 之前拉起的旧字节码 → 需重启激活，非 bug。**
  - #2（captcha 404 storm）：**客户端调用了带尾斜杠的 legacy 路径，源头需确认（疑似陈旧前端构建/缓存标签）→ 非后端代码缺陷，需定位调用方后再决定是否成 batch。**
- 最严重项：captcha 404 retry storm（40 req / 632ms ≈ 63 req/s，MEDIUM，但根因在客户端而非后端逻辑）
- trace_id 贯穿率：**100%**（HTTP 与 Celery 均带 32-hex trace_id）
- 首 token / 慢请求 / LLM 异常 / WS：**无数据**（窗口内无 LLM/SSE/语音业务流量）

## 1. 日志清单

| 文件 | 大小 | 行数 | 时间范围 | 说明 |
|------|------|------|---------|------|
| linchat-backend.log | 24K | 85 | 20:58:45–22:00:53 | 启动 5 行 + **40 对 captcha 404**（WARNING+access），无业务流量 |
| linchat-celery-worker.log | 17K | 126 | 20:58:46–22:00:27 | 周期任务全部 succeeded；11 条 embedding WARNING |
| linchat-celery-beat.log | 4.3K | 41 | 20:58:46–22:00:27 | 调度正常，无异常 |
| linchat-frontend.log | 274B | 11 | 20:58 | Next.js Ready 309ms；1 条 standalone 配置 warn |
| 历史轮转 (.log.1) | — | — | — | 不存在（首次轮转，无跨窗口对比） |

## 2. 错误模式聚类

| 级别 | 模式 | 频次 | 时间窗 | 代表代码位置 | 事实 / 推测 |
|------|------|------|--------|-------------|------------|
| WARNING | `Not Found: /api/v1/auth/captcha/` (404) | 40 | 22:00:53.001–.633（632ms） | 路由 `backend/apps/users/urls.py:13`（`path("captcha", …)` 无尾斜杠）；框架层 `django.request` | 事实：40 个独立 trace_id 的 GET 请求打**带尾斜杠**的 `/captcha/`，路由注册为无尾斜杠 → 404。推测：陈旧客户端 retry storm（见 §8 #1） |
| WARNING | `Doc embedding: attachment not found id=1` | 11 | 21:13:00–21:50:00 | 运行日志 `lineno:56`（旧码 `logger.warning`）；**当前树已改 `backend/apps/media/tasks.py:57` `logger.debug`** | 事实：11 次全部发生在 batch-36 提交(21:57:54)**之前**；运行 worker 是旧字节码（见 §8 #2） |
| INFO | `Embedding health check: … total_failed=0` | 2 | 21:00 / 22:00 | `backend/apps/memory/tasks.py:111` | 事实：embedding 队列 0 积压，健康 |

无 ERROR/CRITICAL/Traceback 可聚类。

## 3. LLM / Agent 错误专项

**无数据。** 窗口内无 LLM 调用 / SubAgent 工具调用 / LangGraph / Langfuse trace 记录。
`LLMConnectionError/LLMTimeoutError/LLMRateLimitError/LLMContentFilterError` 四类 **0 次**。
语音链路（ASR/TTS Gateway）本窗口 **无连接尝试、无失败日志**（无语音流量）。按约定：Gateway 离线属已知外部依赖，即便出现也不作可修 batch。

## 4. 慢请求分析

**HTTP 无数据**（无业务请求，访问日志无 `duration_ms` 埋点）。
Celery 任务耗时全部健康：
- `retry_failed_*` / `expire_guests` / `embedding_health_check`：0.009–0.059s
- `generate_document_embeddings`：0.012–0.024s（均走 not-found 早返回路径）
无超指标项（无 LLM 首 token 场景可测）。

## 5. 可观测性缺口

**trace_id 贯穿：验证通过，贯穿率 100%。**
- HTTP：`django.request` 404 WARNING 与 `uvicorn.access` 共享同一 trace_id（40 对逐一对应）。
- Celery：worker 内每条应用日志携带 32-hex trace_id；beat 无上下文任务由 `core/celery.py` signal 兜底，日志 trace_id 均非 "-"。
- JSONFormatter 正常，字段齐全，无格式化异常/KeyError。

**缺口（既有，非新增）**：backend HTTP 访问日志无 `duration_ms`，日志侧无法算 API p95，依赖 Langfuse 侧观测。

## 6. 沉默失败

本窗口 **无被吞异常触发**。静态既有 broad-except 沉默点（未触发，仅登记）：
- `backend/apps/media/services/document_cache.py` — embedding dispatch `except Exception` → WARNING + 继续（非阻塞设计，合理）。
- `backend/core/celery.py` — 三个 signal 的 `except Exception: pass`（注释明确 signal 不得打断任务）；设计合理，但 trace_id 注入若静默失败将无告警——本窗口未发生。

## 7. 资源告警

- 连接池 / 超时 / pool exhausted：**0 命中**。
- PostgreSQL / Redis 慢查询：**0 命中**。
- WebSocket 异常断开（语音/TTS）：**无数据**（无 WS 流量）。
- Celery 任务堆积：**无**。beat 每 5 分钟调度，worker 即时消费，队列 0 积压；`embedding_health_check` 连续 `stuck_pending=0, stuck_processing=0`。

## 8. 综合问题清单（按优先级）

| # | 问题 | 频次 | 分类 | 对应代码 | 是否成 fix batch | 优先级 |
|---|------|------|------|---------|-----------------|--------|
| 1 | captcha 404 retry storm：40 个独立请求 632ms 内打 `/api/v1/auth/captcha/`（带尾斜杠）全 404 | 40 | **环境/客户端**（非后端逻辑缺陷） | 路由 `backend/apps/users/urls.py:13` 注册为无尾斜杠；当前前端 `authService` 调用**不带**尾斜杠 → storming client 非当前前端源码，疑似陈旧构建/缓存标签/外部探针 | **暂不**。需先定位调用方（见 Q1）。若确认为前端，考虑加 retry 退避 + 构建刷新；若要后端兜底可让路由容忍尾斜杠（触碰 URL 契约，需先问安琳） | MEDIUM |
| 2 | 孤儿 embedding `attachment not found id=1` WARNING | 11 | **运维/部署陈旧**（代码已修） | 运行日志 `lineno:56`=旧 `logger.warning`；**当前树 `apps/media/tasks.py:57` 已是 `logger.debug` + 派发侧 rowcount gate（batch-36, commit 9b8659e）** | **否**（代码已修复）。11 次全部发生在 batch-36 提交(21:57)之前；运行进程 20:58 拉起=旧字节码。**重启 celery worker + backend 即消除**，无需新 batch | LOW |
| 3 | frontend `next start` 与 `output: standalone` 配置不匹配告警 | 1 | 配置 | `frontend/next.config` output 配置 | 否（既有，服务仍 Ready） | LOW |
| 4 | backend HTTP 无 duration 埋点 | — | 可观测性 | 中间件/访问日志 | 否（既有缺口） | INFO |

## 9. Open Questions

1. **Q1（问题#1，captcha 404 storm — 本轮唯一需追根因项）**：40 个带尾斜杠 `/api/v1/auth/captcha/` 请求在 632ms 内爆发（≈63 req/s，各自独立 trace_id），全部 404。事实：路由 `users/urls.py:13` 注册为**无尾斜杠** `captcha`；当前前端 `authService` 调用**无尾斜杠**（若调用会命中 → 200）。故 storming client **不是当前前端源码**。推测：(a) 用户浏览器仍开着旧构建的登录页/缓存标签，验证码刷新在 404 上无退避地紧密重试；或 (b) 外部扫描/探针。**建议确认调用来源（nginx access log / 是否有旧标签打开），再决定是否需要后端路由容忍尾斜杠或前端加退避。** 非阻塞。
2. **Q2（问题#2，确认 batch-36 是否需重启激活）**：代码树 `tasks.py:57`=`logger.debug` 且派发侧已加 rowcount gate；但运行 worker（20:58 启动，早于 21:57 的 batch-36 提交）仍以 `WARNING@line56` 输出旧文案，11 次均在提交前。**结论：batch-36 修复正确、已入树，但尚未在运行进程生效——只需 `./scripts/services.sh restart` 即可让噪声消失（并使 rowcount gate 阻止 id=1 再被派发）。这是运维激活项，不是可修 bug。**
3. **Q3（数据窗口）**：backend 仅 85 行且无 LLM/SSE/语音/WS 业务流量，§3/§4/§7 的业务链路为"无样本"而非"无问题"。**建议跑一轮 E2E（登录+对话+文档+语音）后重采一次**再对语音/首 token/慢请求下结论。

## 10. 数据限制说明

- **无历史轮转文件**（`/tmp/*.log.1` 不存在）：仅 20:58 重启后 ~62min 窗口，无跨天/突发对比。
- **backend 业务流量近零**：LLM/Agent(§3)、慢请求(§4)、WS(§7) 均无数据。"0 代码 bug"结论对 Celery / 埋点 / 日志格式 / signals 链路成立；业务链路需补跑 E2E。
- **运行进程滞后于代码树**：本轮两个 WARNING 聚类均与"进程未重启到最新 batch"相关，诊断已据代码树与 commit 时间线还原，非依赖运行日志文案。
