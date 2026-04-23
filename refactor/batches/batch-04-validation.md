# Batch 04 验证报告

> 验证时间：2026-04-23T15:23:00+08:00
> Validator：batch-validator (ab054fcfa922627b6)
> Worktree：/home/dantsinghua/work/linchat-batch-04
> Branch：refactor/batch-04 (HEAD=552b64c)
> Starting tag：before-batch-04
> Commits 验证范围：22665c8（初始实现）+ 552b64c（finally:reset 时机 bug 原地补丁）

## 1. 自动化验证（plan 5.1 节）

### 1.1 修复前（commit 22665c8，初始实现）

| # | 步骤 | 结果 | 详情 |
|---|------|------|------|
| 1 | `pytest tests/common/test_trace_id.py -v` | PASS | 10/10 通过（0.09s） |
| 2 | `ruff check` 4 个目标文件 | PASS（白名单） | settings.py E402 为预存在 |
| 3 | 全量回归 `pytest backend/` | PASS | 1603 passed / 1 flaky / 9 skipped |
| 4 | H1 import 检查 | PASS | apps.common.trace_id_var + core.middleware.TraceIdMiddleware clean import |

### 1.2 修复后（commit 552b64c，最终版本）

| # | 步骤 | 结果 | 详情 |
|---|------|------|------|
| 1 | `pytest tests/common/test_trace_id.py -v` | PASS | **12/12** 通过（0.07s）— 原 10 + 2 个新增持久化回归保护 |
| 2 | `ruff check` 4 个 batch-04-owned 文件 | PASS（白名单） | 仅 settings.py E402 预存在，非本批次引入 |
| 3 | 全量回归 `pytest backend/` | PASS | **1605 passed** / 1 failed (flaky) / 9 skipped |
| 4 | H1 import 检查 | PASS | 再次确认 |

新增测试（552b64c）：
- `TestTraceIdPersistsAfterMiddleware::test_sync_trace_id_persists_after_middleware_returns`
- `TestTraceIdPersistsAfterMiddleware::test_async_trace_id_persists_after_middleware_returns`

autouse fixture 保证各 test 间隔离（先前 T1/async 的 `reset_after` 断言改为"持久化"语义）。

## 2. 改动一致性核对（两个 commit 合并视角）

| 文件 | plan 预算 | 实际（合并） | 偏差 |
|------|----------|-------------|------|
| backend/apps/common/__init__.py | +19 -1 | +19 -1 | 一致 |
| backend/core/middleware.py | 新文件 ~57 | **+56 -0**（552b64c 删 try/finally，净行数不变） | 一致 |
| backend/core/logging_config.py | 新文件 ~73 | +73 -0 | 一致 |
| backend/core/settings.py | +6 -42 | +6 -42 | 一致 |
| backend/tests/common/test_trace_id.py | 新文件 ~220（计 10 cases） | +261 -0（12 cases, autouse fixture） | +41（R5 已批准；2 个持久化回归） |
| refactor/batches/batch-04-progress.txt | bookkeeping | +28 -0 | 记账类 |
| **合计** | plan ~+375 -43 | **+440 -46** | 增量来自 552b64c bug 补丁（+61 -21） |

所有 6 个 files_touched 均位于 plan 声明 scope（5 个批次内 + 1 个进度记账）；test 扩展 41 行已得 R5 预批准。无 forbidden_zones 跨越。

## 3. 全量回归（plan 5.2 节）

```
============ 1 failed, 1605 passed, 9 skipped, 14 warnings in 53.xxs ============
```

| 指标 | 基线（batch-03 后） | 本次 | 变化 |
|------|---------------------|------|------|
| 通过测试数 | 1592 | **1605** | **+13**（test_trace_id.py 12 + 其它环境差异 1） |
| 失败测试数 | 1（flaky perf） | 1 | 0 净变化 |
| Skipped | 9 | 9 | 不变 |

唯一 failed：`tests/performance/test_smoke.py::TestServiceLayerOverhead::test_message_vo_conversion_performance`
- 预存在 flaky，单独跑通过（1.29s），仅全量并跑时偶发（性能阈值类）
- 与 batch-04 中间件/日志无耦合（内存 VO 转换）
- 属 batch-02 validation 即已记录的同一用例

**结论：无新增回归，修复前后零回归。**

## 4. SLO 验证

batch-04 在 04-refactor-plan.json 中 `blocks_slo: null`，**不需要 SLO 数据对比**。
此批次为 P0 observability 基础设施，为后续 batch-05 / batch-28 以及 performance 分析提供 trace_id 贯穿能力，本身不承载性能指标。

## 5. 手动验证（plan 5.3 节 + 扩展）

使用 `/tmp/swap-and-validate-batch-04.sh` 切换 batch-04 worktree 为 active backend，运行
`/tmp/validate-batch-04.sh` 执行 11 项端到端验证：

| # | 检查 | 结果 | 证据 |
|---|------|------|------|
| Prep.a | 后端跑 batch-04 worktree | PASS | `PID=34303 cwd=/home/dantsinghua/work/linchat-batch-04/backend` |
| Prep.b | HTTP 200（/api/v1/auth/captcha） | PASS | 2xx 返回 |
| M2 | JSON 日志格式统一 | PASS | 最新 10 行 `ok=10 bad=0`，每行含 `trace_id/logger/level/msg` |
| M3.a | X-Request-ID 响应头回写 | PASS | `manual-test-1776928968` |
| M3.b | 自定义 trace_id 落入日志 | PASS | 1 条匹配 |
| M3.c | 跨 logger（200 路径 ≥ 2 种 logger） | WARN（误报） | 200 不触发 `django.request.log_response`，语义等同 M5 通过 |
| M4.a | 无 X-Request-ID 时自动生成 32hex | PASS | `093829ca880d43cf997a43a73808cc29` |
| M4.b | 自动生成 trace_id 出现在日志 | PASS | 1 条匹配 |
| M5.a | 401 响应也回写 X-Request-ID | PASS | `m5-test-1776928969` |
| M5.b | 401 日志含 trace_id | PASS | **2 条（uvicorn.access + django.request 同 trace_id）** |

**核心语义验证**：M5 401 路径样本证明同一 trace_id 同时出现在
`uvicorn.access` 和 `django.request` 两类 logger，即 plan 核心目标
「中间件早于 auth / 贯穿不同日志源」达成。M3.c 的 200 路径仅触发 `uvicorn.access`
是 Django 框架正常行为（< 400 不进 log_response），不构成 FAIL，记为误报 WARN。

### 5.x 典型日志样本（抽 3 条）

```json
{"time":"2026-04-23T15:22:48.700","level":"INFO","logger":"uvicorn.access","trace_id":"093829ca880d43cf997a43a73808cc29","msg":"127.0.0.1:47712 - \"GET /api/v1/auth/captcha HTTP/1.1\" 200",...}
{"time":"2026-04-23T15:22:49.126","level":"WARNING","logger":"django.request","trace_id":"m5-test-1776928969","msg":"Unauthorized: /api/v1/",...}
{"time":"2026-04-23T15:22:49.127","level":"INFO","logger":"uvicorn.access","trace_id":"m5-test-1776928969","msg":"127.0.0.1:47724 - \"GET /api/v1/ HTTP/1.1\" 401",...}
```

**安琳判定：validation pass**（选项 A：原地扩 scope 补丁，2026-04-23 批准）

## 6. Diff 核对（两个 commit）

```
$ git diff before-batch-04..HEAD --stat
 backend/apps/common/__init__.py        |  20 ++-
 backend/core/logging_config.py        |  73 +++++++++
 backend/core/middleware.py            |  56 +++++++
 backend/core/settings.py              |  48 +-----
 backend/tests/common/test_trace_id.py | 261 +++++++++++++++++++++++++++++++++
 refactor/batches/batch-04-progress.txt |  28 +++-
 6 files changed, 440 insertions(+), 46 deletions(-)
```

```
$ git log before-batch-04..HEAD --oneline
552b64c fix(batch-04): middleware 不再 finally:reset，确保 uvicorn/django.request 日志能读到 trace_id
22665c8 feat(observability): 引入 TraceIdMiddleware + 统一 JSON logging (batch-04)
```

所有文件均在 plan `scope.files_touched` 内（R5 已批准 test 文件）；无跨边界改动。

## 7. 中途 fix 追溯（22665c8 → 552b64c）

**触发**：Phase 2c 手动验证阶段，M3.b / M5.b（trace_id 是否出现在日志中）断言失败，日志 `trace_id` 字段回落 `"-"`。

**诊断**：初始实现 `TraceIdMiddleware` 在响应返回后 `finally: trace_id_var.reset(token)`，时机过早——
- `uvicorn.access` 日志在中间件返回后（ASGI lifecycle 尾段）才被写入
- `django.request.log_response`（≥400 路径）亦在 response 发送后才记录

此时 contextvar 已被 reset，JSON formatter 的 `TraceIdFilter` 读不到值 → 回落占位符。

**修复**：删除 try/finally/reset，让 trace_id 在请求生命周期内持久存在于 contextvar，由 uvicorn worker 的下次请求自然覆盖。添加代码注释说明「为什么不 reset」。

**回归保护**：新增 2 个持久化断言测试（sync + async），防止未来 reset 时机回退。

**选项 A 批准**：安琳批复"原地扩 scope 补丁"。commit 552b64c 已 push 至 origin/refactor/batch-04。

**重验**：修复后 `/tmp/validate-batch-04.sh` 端到端 10/11（1 WARN 误报），M3.b/M4.b/M5.b 全部 PASS。

## 8. 最终判定

**STATUS: COMPLETED ✅**

- 自动化验证：12/12 单测通过 + 1605 全量 passed（0 新增回归）
- 改动一致性：6 个文件 100% 落在 plan scope 内（含 R5 预批准 test 扩展）
- 手动验证：端到端 10/11（1 WARN 为框架行为误报，M5 已证核心语义）
- 中途 bug 修复：commit 552b64c 透明追溯，安琳批准选项 A
- 失败测试数：1 → 1（零净变化，唯一 failed 为预存在 flaky perf）

**下一步**：可进入 `/phase2-start batch-05`（dependency 链）或直接启动 `batch-28`（celery Task trace_id 透传，明确 depends_on batch-04）。
