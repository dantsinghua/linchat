# Batch batch-28 执行计划

> 生成时间：2026-07-17 10:58
> 类型：observability | 优先级：P0 | 风险：low
> 预估：3 文件 / 60 行 / 1 session
> 依赖：batch-04 → STATUS: COMPLETED ✅（TraceIdMiddleware / trace_id_var / build_logging_dict 已就绪）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）

## 1. 任务理解（一句话）

用 celery 5.x 的三个 signal（before_task_publish / task_prerun / task_postrun）把发起者上下文里的
`trace_id_var` 透传进 celery Task headers 并在 worker 侧恢复到 contextvar，使 HTTP 请求与其异步
任务（含 beat 周期任务的自动生成值）共享同一 trace_id，配合 batch-04 的 TraceIdFilter 产出可
grep 串联的贯穿日志。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/core/celery.py | 54 | +40 -0 | 注册 3 个 signal handler + import | 低 | 低（54 行，无冗余） |
| 2 | backend/apps/common/__init__.py | 19 | +12 -1 | 新增 ensure_trace_id() + 导出 | 低 | 低（19 行） |
| 3 | backend/tests/common/test_celery_trace_id.py | 0（新增） | +90 | 新增测试 | 低 | — |

三个文件均远低于 300 行硬限制，无拆分需求。

## 3. 详细改动计划

### 文件 2 先行: backend/apps/common/__init__.py

#### 改动 2.1 — 新增 ensure_trace_id() 工具
- 位置：第 16 行 get_trace_id() 之后
- 现状：仅有 `trace_id_var` / `get_trace_id`；celery signal 需要一个"无则生成"的原子工具，
  且 beat 任务需主动生成。中间件里的 `uuid.uuid4().hex` 逻辑应在 common 层复用。
- 改动方案（示意）：
  ```python
  import uuid  # 加到文件顶部 contextvars import 旁

  def ensure_trace_id() -> str:
      """读取当前 trace_id；为空则生成 32 字符 UUID hex 并 set。返回最终值。"""
      tid = trace_id_var.get()
      if not tid:
          tid = uuid.uuid4().hex
          trace_id_var.set(tid)
      return tid
  ```
- 改动理由：统一生成规则（32 hex，与 middleware:30 / chat_service:37 / voice_pipeline:64 一致）；
  供 task_prerun 兜底与 Task 主动调用共用。
- 第 19 行 `__all__` 追加 `"ensure_trace_id"`。
- 预估行数：+12 -1

> 注意：ensure_trace_id() 本身不返回 reset token，因此 task_prerun 里**不直接用它**做 set/reset
> 配对（见改动 1.2 说明），仅供 Task 主动生成或非配对场景使用。prerun 用 `trace_id_var.set()`
> 拿 token。ensure_trace_id 提供的是"规则一致性"复用点。

### 文件 1: backend/core/celery.py

#### 改动 1.1 — import celery signals 与 common 工具
- 位置：第 10-11 行 `from celery import Celery` 附近
- 改动方案：
  ```python
  import uuid
  from celery.signals import (
      before_task_publish, task_prerun, task_postrun,
  )
  from apps.common import trace_id_var
  ```
- 改动理由：signal 装饰器与 contextvar 依赖。
- ⚠️ import 时机：`apps.common` 无 Django 模型依赖（纯 contextvars），在 celery.py 顶层 import
  安全；但为稳妥，signal handler 内部读写 contextvar，import 放模块顶层即可（autodiscover
  在 app 加载后触发，signals 连接在 import 时完成）。
- 预估行数：+5

#### 改动 1.2 — 注册三个 signal handler（文件末尾，第 54 行后）
- 现状：celery.py 无任何 signal；Task 日志 trace_id 恒为 "-"。
- 改动方案（示意，约 30 行）：
  ```python
  # ============ trace_id 透传（batch-28）============
  # 每个 task_id 的 contextvar reset token 暂存表，prerun 存 / postrun 取。
  # prefork worker 单进程串行执行 task，dict 有界（≈ 并发数），无泄漏风险。
  _trace_tokens: dict[str, object] = {}

  @before_task_publish.connect
  def _inject_trace_id(headers=None, **_):
      """发布端：把当前 contextvar trace_id 写进 task headers（protocol v2）。"""
      try:
          if headers is not None:
              tid = trace_id_var.get()
              if tid:
                  headers["trace_id"] = tid
      except Exception:  # noqa: BLE001 — signal 绝不能打断任务发布
          pass

  @task_prerun.connect
  def _restore_trace_id(task_id=None, task=None, **_):
      """worker 端：从 request headers 恢复 trace_id；beat 任务无上下文则生成。"""
      try:
          tid = getattr(task.request, "trace_id", None) if task else None
          if not tid:
              tid = uuid.uuid4().hex  # beat 周期任务兜底
          _trace_tokens[task_id] = trace_id_var.set(tid)
      except Exception:  # noqa: BLE001
          pass

  @task_postrun.connect
  def _clear_trace_id(task_id=None, **_):
      """worker 端：任务结束 reset contextvar，避免 prefork 进程复用时串味。"""
      token = _trace_tokens.pop(task_id, None)
      if token is not None:
          try:
              trace_id_var.reset(token)
          except (ValueError, Exception):  # noqa: BLE001
              trace_id_var.set("")
  ```
- 改动理由：
  - `before_task_publish` 的 `headers` 为 protocol v2 消息头 dict，自定义键会随消息投递并在
    worker 侧暴露为 `task.request.trace_id`（celery 5.x 稳定行为）。
  - `task_prerun` 读 `task.request.trace_id`；beat 调度进程无发起者 contextvar → 生成新 hex。
  - `task_postrun` reset，防止 prefork worker 进程复用导致上个任务 trace_id 残留。
  - Task chain / group：当前代码库**无 chain/group 用法**（rg 确认，仅 shared_task import）。
    但机制天然继承——子任务在父 task_prerun 已 set trace_id 的上下文中发布，
    before_task_publish 会读到父 trace_id 写入子 headers。测试对此加验证用例。
- 预估行数：+35

## 4. 调查步骤（investigation_steps 落实结论）

- [x] I1: `.delay()` / `.apply_async()` 调用点清单（rg 确认，共 4 处）：
  - `apps/memory/services.py:63` generate_embedding.delay — Django 请求内
  - `apps/memory/tasks.py:66` generate_embedding.delay — **task 内调用 task**（retry_failed_embeddings）
  - `apps/media/services/document_cache.py:69` generate_document_embeddings.delay — 请求内
  - `apps/media/tasks.py:128` generate_document_embeddings.delay — task 内
  → 多数发起于请求 lifecycle（TraceIdMiddleware 已 set）；task 内调用者由本 batch 的 prerun
    已 set 上下文 → before_task_publish 天然继承。**无 apply_async 用法。**
- [x] I2: celery 5.6.2（requirements `celery>=5.3.0`，实测 import 版本 5.6.2）。
  before_task_publish/task_prerun/task_postrun signal API 在 5.x 稳定，headers 传递为
  protocol v2 默认行为。
- [x] I3: beat 周期任务共 7 个（celery.py:24-53：daily/monthly summary、retry_failed_embeddings、
  embedding_health_check、clean_expired_media、retry_failed_doc_embeddings、expire_guests）。
  beat 进程无发起者 contextvar → prerun 生成 32 hex 兜底。策略确认可行。
- [x] I4: chain/group 当前**未使用**；机制天然继承，测试补 case 以防未来引入。

**确认结论**：无阻塞，scope 3 文件准确，signal 方案与 batch-04 基础设施完全兼容。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/common/test_celery_trace_id.py -v`（新增，约 5 用例）
  - T1: before_task_publish 把 trace_id_var 当前值写入 headers dict
  - T2: trace_id_var 为空时不污染 headers（不写空串键）
  - T3: task_prerun 从 mock task.request.trace_id 恢复 → trace_id_var.get() 一致
  - T4: task_prerun 无 request.trace_id（beat 场景）→ 生成 32 字符 hex
  - T5: task_postrun 后 trace_id_var reset（不残留）
  - T6（chain 继承）: 模拟父 prerun set → 子 before_task_publish 读到父 trace_id
  - T7: ensure_trace_id() 空→生成 hex / 非空→原值返回
- [ ] `ruff check backend/core/celery.py backend/apps/common/__init__.py backend/tests/common/test_celery_trace_id.py`
- [ ] `pytest backend/tests/common/ backend/tests/memory/test_tasks.py -v`（回归，确认 signal 不破坏现有 task 测试）

### 5.2 手动验证步骤
- [ ] HTTP 请求触发 `.delay()`（如上传文档触发 generate_document_embeddings）→
      对比 web 日志与 `/tmp/linchat-celery.log` 中 trace_id 一致
- [ ] 触发 beat daily_summary，`grep trace_id /tmp/linchat-celery.log` → 32 字符 hex（自动生成）
- [ ] task 内调用 task（retry_failed_embeddings → generate_embedding）→ 子任务日志 trace_id 与父一致

### 5.3 性能验证
- 不适用（P0 可观测性，非 P1 性能 batch）。signal handler 为纯内存 contextvar 操作，开销可忽略。

### 5.4 回归验证
- [ ] `pytest backend/tests/memory/ backend/tests/media/ backend/tests/common/ -v`
      （涉及 celery task 的 app + common 基础设施）
- [ ] 确认 CELERY signal 不影响 `CELERY_TASK_ALWAYS_EAGER` 未开启下的现有 mock 型 task 测试

## 6. 回滚策略

复述 04-refactor-plan.json：`git revert <commit>`；celery signals 移除后 Task 日志 trace_id
回落到 "-"，无业务影响。

具体操作：
```bash
git revert <commit-hash>
# 或整批 worktree 撤销
cd ..
git worktree remove linchat-batch-28
git branch -D refactor/batch-28
```

## 7. ⚠️ 需要安琳确认的事项

- ✅ 无阻塞事项，可直接进入 executor 阶段。

补充说明（非阻塞，仅告知）：
- scope 3 文件与调查结论完全一致，无扩大需求。新增测试 `test_celery_trace_id.py` 按安琳先例
  不算扩 scope。
- 未触碰任何 do_not_touch 区域（无 schema/SSE/加密/session_id/版本升级/Docker/前端/Gateway 变更）。
- `_trace_tokens` 模块级 dict 在 prefork worker 下有界；若未来切 threads/gevent 池，需复核
  contextvar 隔离——当前 prefork 无此问题，已在代码注释标注。
- signal handler 全部 `try/except` 吞异常，确保绝不打断任务发布/执行（可观测性代码不得影响业务）。

## 8. 执行预算

- 预计 tool calls：约 15-20（读 2 文件改动点 + 写 3 文件 + 跑 pytest/ruff 验证）
- 预计 token 消耗：中等（单 session 内完成）
- 预计完成时间：1 session（与 estimated_sessions=1 一致）

未超预算，无需拆分。
