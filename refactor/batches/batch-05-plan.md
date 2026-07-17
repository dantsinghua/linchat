# Batch batch-05 执行计划

> 生成时间：2026-04-23
> 类型：observability | 优先级：P0 | 风险：low
> 预估：6 文件 / ~80 行 / 1 session
> 依赖：batch-04 ✅ COMPLETED（552b64c）
> SLO 影响：无；为后续 obs / perf batch 提供业务层 trace 关联能力

## 1. 任务理解（一句话）

把 batch-04 已上线的 `apps.common.trace_id_var` / `X-Request-ID` 契约**真正接入 chat→graph 业务链路**：让 `ChatService.send_message` 入口复用 HTTP `trace_id` 作为 `request_id`，让 Langfuse callback / `LangGraphExecution.langfuse_trace_id` / gateway 子请求 `X-Request-ID` / SSE 异常日志 / finalize 失败路径全部能读到**同一个** trace_id，使一次前端请求在日志 / Langfuse / gateway 三侧可串联。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/chat/services/chat_service.py | 144 | +5 -2 | send_message 入口改复用 + log extra | 低 | 低 |
| 2 | backend/apps/graph/services/agent_service.py | 246 | +15 -5 | contextvar set + 热点 log extra | 中（热点） | 中 |
| 3 | backend/apps/graph/services/helpers/prompt.py | 86 | +1 -1 | memory warning 改 extra | 低 | 低 |
| 4 | backend/apps/graph/services/helpers/finalize.py | 111 | +8 -2 | 新增失败日志 + 注释澄清 | 低 | 低 |
| 5 | backend/apps/common/sse.py | 90 | +4 -2 | SSE 异常日志 extra（heartbeat wire 保持） | 低 | 低 |
| 6 | backend/apps/common/gateway_utils.py | 128 | +6 -1 | header 默认 contextvar + span metadata | 低 | 中（13 处 E701 预存债，不修） |
| **合计** | | **805** | **+39 -13** | | | |

**硬限制**：所有文件均 < 300 行。`agent_service.py` 246 行加 10 后仍 <260，安全。
**ruff**：`gateway_utils.py` 13 个 E701（batch-03 精简残留），**不在 batch-05 scope**。

## 3. 详细改动计划

### 文件 1：chat_service.py

#### 改动 1.1 — send_message 复用 trace_id（第 37 行）

- 当前：`request_id = uuid.uuid4().hex`
- 改动：
  ```python
  from apps.common import get_trace_id  # 顶部
  ...
  # batch-05：HTTP 路径复用 TraceIdMiddleware 设置的 trace_id；
  # 非 HTTP 路径（voice WS / pytest 直调）回退 uuid4().hex，保留历史语义
  request_id = get_trace_id() or uuid.uuid4().hex
  logger.info("chat send start", extra={"user_id": user_id, "request_id": request_id})
  ```
- 理由：TraceIdMiddleware 在 MIDDLEWARE 顶端、异步 view（chat/views.py:50）执行时 contextvar 已 set。fallback 保留 voice_pipeline.py:64 等独立 request_id 路径。
- 行数：+4 -1

#### 改动 1.2 — stop/resume 日志改 extra（第 47 行等）

- 当前：`logger.info(f"Stop signal sent for request {request_id}")`
- 改动：`logger.info("stop signal sent", extra={"user_id": user_id, "request_id": request_id})`
- 理由：JSONFormatter 已支持 extra 序列化（batch-04）。
- 行数：+1 -1

### 文件 2：agent_service.py

#### 改动 2.1 — execute / resume 顶部显式 set contextvar（第 33, 209 行）

- 改动：
  ```python
  from apps.common import trace_id_var  # 顶部
  ...
  async def execute(user_id, thread_id, request_id, user_message, attachment_uuids=None):
      # batch-05：Voice / Celery / 测试 不经 HTTP middleware，contextvar 为空；
      # 显式 set，保证 helpers/prompt/finalize/gateway 日志拿得到 trace_id。
      # HTTP 路径 middleware 已 set，这里是幂等覆盖（值相同）。
      _tid_token = trace_id_var.set(request_id)
      try:
          ...  # 现有 execute 全部代码
      finally:
          ...  # 现有 cleanup
          trace_id_var.reset(_tid_token)  # 必须在最后（在 unregister_generation 之后）
  ```
- ⚠️ **batch-04 教训**：middleware 因 finally reset 太早导致 uvicorn.access 日志 trace_id="-"。**此处不同**：agent 的所有日志发生在 reset 之前，reset 放尾部无负作用。
- 理由：voice_pipeline.py:64 自生成 uuid4().hex 走 AgentService.execute，不经 middleware；若不 set，helpers 里的 log extra trace_id 为 "-"。
- 行数：+6 -0（execute）、+4 -0（resume）

#### 改动 2.2 — 6 处热点 logger 改 extra（第 167/195/197/235/243/245 行）

- 示例：`logger.exception("Agent execution error", extra={"request_id": request_id, "user_id": user_id})`
- 不碰其余 logger（保留现状以控 scope）
- 行数：+5 -5（净 0）

### 文件 3：helpers/prompt.py

#### 改动 3.1 — memory search 失败 warning 改 extra（第 43 行）

- 当前：`logger.warning("Memory recall failed for user %d: %s", user_id, e)`
- 改动：`logger.warning("memory recall failed", extra={"user_id": user_id, "error": repr(e)})`
- 行数：+1 -1

### 文件 4：helpers/finalize.py

#### 改动 4.1 — handle_execution_failure 补日志（第 36-50 行）

- 当前：函数体无 logger 调用（线上排障 blind spot）
- 改动：
  ```python
  import logging  # 顶部新增
  logger = logging.getLogger(__name__)  # 顶部新增
  ...
  async def handle_execution_failure(...):
      end_time = timezone.now()
      duration_ms = int((end_time - start_time).total_seconds() * 1000)
      logger.warning("execution failed", extra={
          "request_id": execution.request_id, "error_type": error_type,
          "error_message": str(error_message)[:200], "duration_ms": duration_ms,
      })
      finalize_execution(...)
  ```
- 理由：现有实现沉默入库失败，没日志。
- 行数：+6 -0

#### 改动 4.2 — langfuse_trace_id 注释澄清（第 28-29 行，**仅注释不改代码**）

- 理由：让 reviewer 清楚 `langfuse_handler.last_trace_id`（Langfuse 内部 hex）与 `request_id`（X-Request-ID）是两个独立 id，保留现状。
- 行数：+3 注释

### 文件 5：apps/common/sse.py

#### 改动 5.1 — heartbeat wire 不变，仅加 debug 日志（第 63 行）

- 改动：heartbeat data 保持 `{"type":"heartbeat"}` 原样，新增：
  ```python
  logger.debug("sse heartbeat", extra={"context_name": context_name, "user_id": user_id})
  ```
- 理由：前端零改动。trace_id 已由首 StreamChunk 的 request_id 字段 + HTTP 响应头 X-Request-ID 两处提供。heartbeat wire 加 trace_id 会扩 scope 到前端。见第 7 节 Q2。
- 行数：+1 -0

#### 改动 5.2 — cancelled / error 日志改 extra（第 76, 79 行）

- 改动：`logger.info("sse cancelled", extra={...})` / `logger.exception("sse error", extra={...})`
- 行数：+2 -2

### 文件 6：apps/common/gateway_utils.py

#### 改动 6.1 — build_gateway_headers 默认继承 contextvar（第 19-27 行）

- 当前：
  ```python
  if not request_id:
      request_id = str(uuid.uuid4())  # ⚠️ 带横线 36 字符，与 trace_id hex 不同格式
  headers["X-Request-ID"] = request_id
  ```
- 改动：
  ```python
  from apps.common import get_trace_id  # 延迟 import 避免启动期循环（函数内部）
  ...
  if not request_id:
      request_id = get_trace_id() or uuid.uuid4().hex  # 统一 hex 格式
  headers["X-Request-ID"] = request_id
  ```
- 理由：调用方 media.services.document / users.member_service / reset_all_data 多处不传 request_id，当前会生成与父 trace 脱离的新 id。改为优先继承 contextvar，兜底 hex 与 trace 格式一致。
- ⚠️ **格式变化**：见第 7 节 Q3（Gateway 侧是否有 UUID 格式校验）
- 行数：+3 -1

#### 改动 6.2 — record_gateway_span metadata 加 trace_id（第 115-118 行）

- 改动：metadata dict 增加 `"trace_id": get_trace_id() or request_id or ""`
- 理由：Langfuse UI 可按 metadata.trace_id 聚合同一链路多个 gateway span（document_parse + LLM 调用）。
- 行数：+2 -0

## 4. 调查步骤（已完成确认清单）

- [x] H1：`apps.common.trace_id_var` / `get_trace_id` 已导出（__init__.py:11,14）
- [x] H2：voice_pipeline.py:64 自生成 uuid4().hex 走 AgentService.execute，不经 HTTP middleware，改动 2.1 的 `trace_id_var.set` 确保其日志也带 trace_id
- [x] H3：`Langfuse.last_trace_id`（Langfuse 内部 hex）与 `request_id`（HTTP X-Request-ID）是两个字段；langfuse_trace_id 字段保留现状
- [x] H4：StreamChunk.request_id 是前端契约（agent_service.py:126）；改动 1.1 让 request_id == trace_id 后仍是合法 32-hex，前端零破坏
- [x] H5：gateway_utils.py 13 个 E701 是 batch-03 精简残留，不在 batch-05 scope
- [x] H6：init_langfuse（monitor.py:20）已用 `trace_context={"trace_id": request_id}`，改动 1.1 后 Langfuse trace_id 自动等于 HTTP trace_id，无需改 monitor.py

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/chat/ -v`（重点 test_services.py / test_concurrency.py）
- [ ] `pytest backend/tests/apps/graph/ -v`
- [ ] `pytest backend/tests/integration/test_sse_async.py -v`
- [ ] `pytest backend/tests/common/test_trace_id.py -v`（batch-04 12 cases 不应回归）
- [ ] `ruff check` 6 个目标文件 → 不新增 error（允许 gateway_utils 预存 13 E701）
- [ ] 全量：`pytest backend/ 2>&1 | tail -10` → ≥ 1605 passed / 0 新增失败

### 5.2 手动验证
- [ ] `./scripts/services.sh restart` + 日志 JSON 可解析
- [ ] E2E：
  ```bash
  TID="batch05-e2e-$(date +%s)"
  curl -s -D - -H "X-Request-ID: $TID" -X POST http://localhost:8002/api/v1/chat/ \
       -H "Cookie: linchat_token=..." -d '{"content":"你好"}'
  grep "$TID" /tmp/linchat-backend.log | jq -r '.logger' | sort -u
  # 预期 logger 列表含：chat_service / agent_service / helpers.* / uvicorn.access / django
  grep "$TID" /tmp/linchat-backend.log | wc -l  # 预期 ≥ 10
  ```
- [ ] 前端 Network Tab：响应头 X-Request-ID 等于 DB Message.request_id
- [ ] Langfuse UI：最新 chat trace.trace_id == X-Request-ID
- [ ] 多模态：上传图聊天，gateway span metadata.trace_id == X-Request-ID
- [ ] 并发：同时发 2 条不同 X-Request-ID，日志各自 grep 无串扰（Q4 验证）

### 5.3 性能验证
不适用（observability 类）。

### 5.4 回归验证
- [ ] 语音路径：`pytest backend/tests/voice/ -v`（voice_pipeline 仍用自生成 uuid4().hex 不被破坏）
- [ ] 监控面板：`context_status` SSE 事件的 request_id 字段 == trace_id

## 6. 回滚策略

`git revert <commit>` 单 commit。

**数据影响**：Message.request_id / LangGraphExecution.request_id 字段值从 uuid4.hex（32-hex）变为 trace_id（仍 32-hex），schema 无变化，历史数据兼容。
**下游**：后续 batch-06/07 扩展 log extra 字段，不反向依赖 batch-05 具体改动，可独立回滚。

## 7. Open Questions（已由安琳 2026-04-23 拍板）

- [x] **Q1（request_id 去留）= A** — 保留 `request_id` 字段名不改，语义等于 trace_id；零 schema/前端契约改动。
- [x] **Q2（SSE heartbeat wire）= A** — wire 保持 `{"type":"heartbeat"}` 不变，仅后端 debug 日志带 trace_id；前端零改动。
- [x] **Q3（Gateway X-Request-ID 格式）= 统一 hex** — 安琳确认 llm-gateway 无 UUID 格式校验，推进改动 6.1 完整方案（带横线 uuid4 → 32-hex）。
- [x] **Q4（agent_service 双重 contextvar set）= 接受** — middleware + execute 幂等 set；ASGI coroutine 天然隔离，无串扰风险。

**✅ 无阻塞，可进入 executor。**

## 8. 执行预算

- Tool calls：~20（6 Edit + 4 Read + 7 Bash 验证 + 2 Write + 1 其他）
- Token：~30k input / ~8k output
- 时间：1 session（30-45 分钟），在 `estimated_sessions=1` 内

## 9. 预期效果对比

| 指标 | batch-04 后 | batch-05 后 |
|------|-------------|-------------|
| trace_id 贯穿 HTTP → middleware → log | ✅ | ✅ |
| trace_id 贯穿 chat_service → agent_service | ❌ | ✅ |
| trace_id 贯穿 Langfuse trace | Langfuse 内部 hex（独立） | 与 HTTP trace_id 一致 |
| Gateway 子请求 X-Request-ID 继承父 trace | ❌（每调用新 uuid4） | ✅ |
| handle_execution_failure 有日志 | ❌ | ✅ |
| voice 路径日志 trace_id | "-" | agent 自 set 后有值 |

---

**状态**：PLAN_READY — 等待安琳 review Q1-Q4。
