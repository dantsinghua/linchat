# Batch batch-04 执行计划

> 生成时间：2026-04-22
> 类型：observability | 优先级：P0 | 风险：medium
> 预估：4 文件 / 150 行 / 1 session
> 依赖：无（depends_on=[]）
> SLO 影响：无直接阻塞；后续所有 observability + performance batch 的地基

## 1. 任务理解（一句话）

为后端建立 **trace_id 贯穿基础设施**：HTTP 入口 `TraceIdMiddleware` 读取 / 生成 `X-Request-ID` → 存入 `contextvars.ContextVar` → 通过自定义 `logging.Filter/Formatter` 注入到所有日志；把三种混合日志格式（应用 / uvicorn / Django）统一为带 `trace_id` 的 **JSON 结构化格式**；仅改 `core/` + `apps/common/__init__.py`，不碰业务代码。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/core/middleware.py | **不存在** | +55 -0 | 新建 | 中（ASGI 异步兼容） | — |
| 2 | backend/core/logging_config.py | **不存在** | +80 -0 | 新建（JSON Formatter + Filter） | 中（影响所有日志） | — |
| 3 | backend/core/settings.py | 513 | +5 -42 | 修改 MIDDLEWARE / LOGGING | 中（热点 25 次修改） | 低（ruff 干净） |
| 4 | backend/apps/common/__init__.py | 1 | +15 -1 | 导出 trace_id_var | 低 | 低 |
| **合计** | | **514** | **+155 -43** | | | |

**精简潜力说明**：scope 内现存文件均 ruff F401 干净。settings.py 513 行超 300 软限制，04-refactor-plan 已规划 settings 域拆分 batch，本 batch 不扩 scope。新增 `logging_config.py` ~80 行 / `middleware.py` ~55 行均低于 300 硬限制。本 batch 顺带让 settings.py 从 513 行 → ~476 行（LOGGING 字面量被函数替换）。

## 3. 详细改动计划

---

### 文件 1：backend/core/middleware.py（新建）

#### 改动 1.1 — 创建 TraceIdMiddleware（同步 + ASGI 双兼容）

- 位置：全新文件
- 改动方案：
  ```python
  """Trace ID 贯穿中间件 — batch-04 可观测性基础设施。"""
  import uuid
  from typing import Callable

  from django.http import HttpRequest, HttpResponse
  from asgiref.sync import iscoroutinefunction

  from apps.common import trace_id_var

  TRACE_HEADER = "HTTP_X_REQUEST_ID"
  RESP_HEADER = "X-Request-ID"


  class TraceIdMiddleware:
      """为每个 HTTP 请求分配 / 继承 trace_id。MIDDLEWARE 顶端注册。"""

      sync_capable = True
      async_capable = True

      def __init__(self, get_response: Callable):
          self.get_response = get_response
          self._is_async = iscoroutinefunction(get_response)

      def _extract_or_generate(self, request: HttpRequest) -> str:
          incoming = request.META.get(TRACE_HEADER, "").strip()
          if incoming and len(incoming) <= 128:
              return incoming
          return uuid.uuid4().hex  # 与 chat_service:37 / voice_pipeline:64 一致

      def __call__(self, request: HttpRequest):
          if self._is_async:
              return self._acall(request)
          return self._scall(request)

      def _scall(self, request: HttpRequest) -> HttpResponse:
          tid = self._extract_or_generate(request)
          token = trace_id_var.set(tid)
          try:
              request.trace_id = tid
              response = self.get_response(request)
              response[RESP_HEADER] = tid
              return response
          finally:
              trace_id_var.reset(token)

      async def _acall(self, request: HttpRequest) -> HttpResponse:
          tid = self._extract_or_generate(request)
          token = trace_id_var.set(tid)
          try:
              request.trace_id = tid
              response = await self.get_response(request)
              response[RESP_HEADER] = tid
              return response
          finally:
              trace_id_var.reset(token)
  ```
- 改动理由：
  - Django 4.2 ASGI 栈同时存在 sync / async view，必须 `sync_capable + async_capable` 双实现，用 `iscoroutinefunction(get_response)` 判断路径
  - `uuid.uuid4().hex`（32 字符无横线）与现有 `chat_service.py:37` / `voice_pipeline.py:64` 格式一致
  - `len <= 128` 防御超长恶意 header
  - 响应 header 回写：便于前端把截图/录屏关联后端日志
  - 不在 middleware 内打 log，避免与 TokenAuthMiddleware 日志风暴
- 预估行数：+55 -0

---

### 文件 2：backend/core/logging_config.py（新建）

#### 改动 2.1 — TraceIdFilter + JSONFormatter + build_logging_dict()

- 位置：全新文件
- 改动方案：
  ```python
  """LinChat 统一日志配置 — batch-04 可观测性基础设施。"""
  from __future__ import annotations

  import datetime as dt
  import json
  import logging
  from typing import Any

  from apps.common import trace_id_var


  class TraceIdFilter(logging.Filter):
      def filter(self, record: logging.LogRecord) -> bool:
          record.trace_id = trace_id_var.get() or "-"
          return True


  class JSONFormatter(logging.Formatter):
      _RESERVED = {
          "args", "asctime", "created", "exc_info", "exc_text", "filename",
          "funcName", "levelname", "levelno", "lineno", "message", "module",
          "msecs", "msg", "name", "pathname", "process", "processName",
          "relativeCreated", "stack_info", "thread", "threadName", "trace_id",
      }

      def format(self, record: logging.LogRecord) -> str:
          payload: dict[str, Any] = {
              "time": dt.datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
              "level": record.levelname,
              "logger": record.name,
              "trace_id": getattr(record, "trace_id", "-"),
              "msg": record.getMessage(),
              "module": record.module,
              "lineno": record.lineno,
          }
          for key, value in record.__dict__.items():
              if key in self._RESERVED or key.startswith("_"):
                  continue
              try:
                  json.dumps(value)
                  payload[key] = value
              except (TypeError, ValueError):
                  payload[key] = repr(value)
          if record.exc_info:
              payload["exc_info"] = self.formatException(record.exc_info)
          return json.dumps(payload, ensure_ascii=False)


  def build_logging_dict(debug: bool, log_level: str = "INFO") -> dict[str, Any]:
      _flt = ["trace_id"]
      return {
          "version": 1,
          "disable_existing_loggers": False,
          "filters": {"trace_id": {"()": "core.logging_config.TraceIdFilter"}},
          "formatters": {
              "json": {"()": "core.logging_config.JSONFormatter"},
              "verbose": {"format": "{levelname} {asctime} [{trace_id}] {module} {message}", "style": "{"},
              "simple": {"format": "{levelname} {asctime} [{trace_id}] {message}", "style": "{"},
          },
          "handlers": {
              "console": {"class": "logging.StreamHandler", "formatter": "json", "filters": _flt},
          },
          "root": {"handlers": ["console"], "level": log_level},
          "loggers": {
              "django": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
              "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False, "filters": _flt},
              "uvicorn": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
              "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False, "filters": _flt},
              "uvicorn.error": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
              "apps": {"handlers": ["console"], "level": "DEBUG" if debug else log_level, "propagate": False, "filters": _flt},
              "apps.context.monitoring": {"handlers": ["console"], "level": "DEBUG", "propagate": False, "filters": _flt},
          },
      }
  ```
- 改动理由：
  - 单文件导出 3 对象；settings.py 只保留 2 行 import+调用
  - 保留 `verbose` / `simple` 旧 formatter（附带 `[trace_id]`），pytest -s 调试可用
  - 覆盖 uvicorn.access / uvicorn.error / django / django.request / apps — 02-issue-diagnosis §5.2 列出的"三种格式混合"根源
  - extra 字段自动序列化（`logger.info("msg", extra={"user_id": 7, "duration_ms": 123})`）
  - 非 JSON 可序列化字段用 `repr()` 兜底，永不丢日志
  - **不引入** `python-json-logger` 新依赖，避免触发 CLAUDE.md 红线
- 预估行数：+80 -0

---

### 文件 3：backend/core/settings.py（修改）

#### 改动 3.1 — MIDDLEWARE 顶端注册（第 57-69 行）

- 当前：
  ```python
  MIDDLEWARE = [
      "corsheaders.middleware.CorsMiddleware",
      "django.middleware.security.SecurityMiddleware",
      ...
      "apps.common.middleware.TokenAuthMiddleware",
  ]
  ```
- 改动方案：
  ```python
  MIDDLEWARE = [
      # trace_id 必须最先执行，使所有后续中间件 / 视图 / 日志可读 trace_id（batch-04）
      "core.middleware.TraceIdMiddleware",
      "corsheaders.middleware.CorsMiddleware",
      "django.middleware.security.SecurityMiddleware",
      ...
      "apps.common.middleware.TokenAuthMiddleware",
  ]
  ```
- 改动理由：`TraceIdMiddleware` 放在最前，使 OPTIONS 预检 + CORS 拒绝 + Token 401 全部日志都带 trace_id。保留注释防误改。
- 预估行数：+2 -0

#### 改动 3.2 — LOGGING 替换为 build_logging_dict 调用（第 472-513 行）

- 当前：42 行 dict 字面量
- 改动方案：
  ```python
  # 日志配置 — 统一 JSON + trace_id 注入（batch-04）
  from core.logging_config import build_logging_dict
  LOGGING = build_logging_dict(debug=DEBUG, log_level=os.getenv("DJANGO_LOG_LEVEL", "INFO"))
  ```
- 改动理由：把 42 行压到 3 行；保留 `DJANGO_LOG_LEVEL` 环境变量兼容。
- 预估行数：+3 -42 = **净 -39**

**警告**：settings.py 是 Git Top1 修改热点（25 次），仅动 MIDDLEWARE 顶部 + LOGGING 块，不触碰其他 117 个 getenv，降低 rebase 冲突风险。

---

### 文件 4：backend/apps/common/__init__.py（修改）

#### 改动 4.1 — 导出 trace_id_var + get_trace_id()

- 位置：全文件（当前仅 `# 公共组件模块`）
- 改动方案：
  ```python
  """公共组件模块 — 基础设施工具集合。"""
  from __future__ import annotations

  import contextvars

  # trace_id 全局上下文变量（batch-04）
  # 由 core.middleware.TraceIdMiddleware 在请求进入时 set；
  # 由 core.logging_config.TraceIdFilter 读取注入到每条日志。
  # 其他模块（celery / voice consumer / langgraph）如需主动覆盖，
  # 请 trace_id_var.set(...) 并保留 Token 用于 .reset()。
  trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


  def get_trace_id() -> str:
      """读取当前上下文 trace_id；无则返回空串。"""
      return trace_id_var.get() or ""


  __all__ = ["trace_id_var", "get_trace_id"]
  ```
- 改动理由：
  - `from apps.common import trace_id_var` 最短最稳
  - `core.middleware` / `core.logging_config` 反向 import `apps.common`：实际安全 — common 无 ORM 触发路径，仅 contextvars（纯 stdlib）
  - 循环风险若真出现，回退方案见第 7 节 R1
- 预估行数：+15 -1 = 净 **+14**

## 4. 调查步骤

observability 类 batch，但有 3 个执行前必须验证的假设：

- [ ] **H1（循环依赖）**：
  ```bash
  cd /home/dantsinghua/work/linchat-batch-04/backend
  source /home/dantsinghua/work/linchat/linchat/bin/activate
  python -c "from apps.common import trace_id_var; print(trace_id_var)"
  python -c "import django; django.setup(); from core.middleware import TraceIdMiddleware; print(TraceIdMiddleware)"
  ```
  预期：无 `ImportError`

- [ ] **H2（ASGI async 路径）**：
  ```bash
  curl -v -H "X-Request-ID: test-batch-04-h2" http://localhost:8002/api/v1/auth/captcha 2>&1 | grep -i "x-request-id"
  ```
  预期：响应头 `X-Request-ID: test-batch-04-h2`

- [ ] **H3（JSON 日志不破坏 uvicorn.access）**：
  ```bash
  tail -f /tmp/linchat-backend.log | grep -E '"logger":"uvicorn.access"' | head -3
  ```
  预期：单行 JSON 含 `logger":"uvicorn.access"` + 原 IP/method/status 文本合并到 `msg` 字段

## 5. 验证计划

### 5.1 自动化验证

- [ ] 新增测试文件 `backend/tests/common/test_trace_id.py`（见第 7 节 R5）
  - T1：无 header → 自动生成 32 字符 UUID hex
  - T2：有 header → 继承该值
  - T3：> 128 字符 header → 丢弃重新生成
  - T4：响应头回写
  - T5：`TraceIdFilter.filter()` 空 contextvars 注入 `"-"`
  - T6：`JSONFormatter.format()` 输出合法 JSON（`json.loads` round-trip）
- [ ] `pytest backend/tests/common/test_trace_id.py -v` → 6 PASSED
- [ ] 无回归：`pytest backend/tests/ 2>&1 | tail -10` → 1586+6 passed
- [ ] ruff：`ruff check backend/core/middleware.py backend/core/logging_config.py backend/core/settings.py backend/apps/common/__init__.py`

### 5.2 手动验证

- [ ] 重启：`./scripts/services.sh restart`
- [ ] JSON 首行可解析：
  ```bash
  head -10 /tmp/linchat-backend.log | python -c "import json,sys;[json.loads(l) for l in sys.stdin];print('OK')"
  ```
- [ ] 自定义 trace_id 端到端：
  ```bash
  curl -s -H "X-Request-ID: manual-test-001" http://localhost:8002/api/v1/health/ -o /dev/null -D -
  grep "manual-test-001" /tmp/linchat-backend.log | head -5
  ```
  预期：5+ 条含该 trace_id 的日志，涵盖 uvicorn.access + django + apps 三类 logger
- [ ] 无 trace_id 请求：响应头含新生成 UUID hex，日志 trace_id ≠ "-"

### 5.3 性能验证

不适用。预期 overhead < 1ms/request。安琳如要求量化：`ab -n 100 -c 10` 前后对比 mean Δ < 5%。

### 5.4 回归验证

- [ ] 全量：`pytest backend/ -v 2>&1 | tail -30` → 1592 passed / 0 failed
- [ ] 跨模块冒烟：`pytest backend/tests/users/ backend/tests/chat/ backend/tests/voice/ -v`
- [ ] 浏览器：登录 → 网络面板看 `X-Request-ID` 响应头；发聊天 → `grep <trace_id>` 能串联 chat.views + graph.agent_service

## 6. 回滚策略

单 commit revert，干净无残留：
```bash
git revert <commit-hash>
# 或 worktree 级：
cd /home/dantsinghua/work && git worktree remove linchat-batch-04 && git branch -D refactor/batch-04
```

**数据影响**：无。仅影响日志格式 + 响应头。不动 DB / Redis / MinIO。

**下游依赖**：后续 observability batch 预期 `depends_on: ["batch-04"]`，回滚本 batch 必须连同下游一起回滚。

## 7. ✅ 安琳已批复决策（2026-04-23）

- [x] **R1（trace_id_var 位置）**：安琳批复 → `apps/common/__init__.py`
  - 理由：import 路径最短，与 scope 吻合
  - 循环依赖由第 4 节 H1 验证

- [x] **R2（MIDDLEWARE 顺序）**：安琳批复 → TraceId 在 `CorsMiddleware` **之前**
  - 效果：OPTIONS 预检 + CORS 拒绝日志均带 trace_id

- [x] **R3（JSON Formatter 本地调试）**：安琳批复 → 本地 + 生产统一 JSON，本地用 `jq` 美化
  - 前置已确认：`/usr/bin/jq` v1.6 已安装（2026-04-23 验证）

- [x] **R4（celery worker trace_id 透传）**：安琳批复 → 本 batch 不处理，延后至**新增 batch-28**
  - 已写入 `refactor/04-refactor-plan.json` → `batch-28: celery Task trace_id 透传`
  - 已加入 `phased_rollout.phase_p0_observability`（batch-04 → 05 → 06 → 07 → 28）
  - 本 batch 仅通过 `build_logging_dict()` 让 celery logger 能拾取 contextvars；Task 侧未透传时 trace_id 字段为 `"-"`

- [x] **R5（新增测试是否算扩 scope）**：安琳批复 → 新增测试**不算扩 scope**
  - `backend/tests/common/test_trace_id.py` 按本 batch 第 5.1 节 T1-T6 执行

**R1-R5 已全部解决，可进入 `/phase2-execute batch-04`。**

## 8. 执行预算

- Tool calls：~18（3 Write + 2 Edit + 6 Bash 验证 + 其他）
- Token：~40k input / ~12k output
- 时间：1 session（40-60 分钟），在 `estimated_sessions=1` 内

## 9. 预期效果对比

| 指标 | 前 | 后 |
|------|----|----|
| trace_id 覆盖率 | 0 | 100% 日志行 |
| 日志格式 | 3 种混合 | 统一 JSON |
| X-Request-ID 响应头 | 无 | 所有 HTTP 响应 |
| settings.py 行数 | 513 | ~476（LOGGING 块被函数替换） |
| 工具链 | grep 文本 | `jq .trace_id`、loki/kibana 直接摄入 |
| 下游 batch | 被 block | P0 obs ×3 + P1 perf ×2 可启动 |

---

**状态**：PLAN_READY — 等待安琳 review 第 7 节 R1-R5。
