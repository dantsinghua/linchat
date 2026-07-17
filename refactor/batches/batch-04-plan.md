# Batch batch-04 执行计划

> 生成时间：2026-07-17
> 类型：observability | 优先级：P0 | 风险：medium
> 预估：4 文件 / ~150 行 / 1 session
> 依赖：无（depends_on=[]，无需前置校验）
> SLO 影响：blocks_slo=null；但 notes 明确"此 batch 是后续所有 observability 和 performance batch 的基础"

## 1. 任务理解（一句话）

新建 `TraceIdMiddleware`（从 `X-Request-ID` header 提取或生成 trace_id 存入 contextvars）+ 统一
JSON logging（TraceIdFilter/JSONFormatter），把当前 uvicorn/django/apps 三种混合日志格式收敛为一条
带 `trace_id` 字段的 JSON，让全链路日志可按请求关联。

## 2. 关键背景：旧分支 refactor/batch-04（未合并）

April 时期的旧分支 `refactor/batch-04` 已含**完整可用实现**（commit 22665c8 feat + 552b64c fix +
ee840bf validate，pytest 全量 1603 passed）。本计划以其为**参考蓝本**，但所有行号/差异基于**当前
main** 编写。旧分支不能直接 cherry-pick（4 月后 main 已演进，需人工对齐 settings.py 行号）。

**旧分支最重要的一条经验（commit 552b64c）**：middleware **绝不能在 `finally` 里 `reset()`
contextvar**。因为 `uvicorn.access` 与 `django.request` 的 `log_response` 都在本 middleware
`return` 之后才写日志，一旦 reset，它们读到的 trace_id 永远是 `-`。依赖 asyncio.Task /
sync_to_async 的 contextvars 天然隔离即可，无需手动 reset。**本计划强制保留此设计**。

## 3. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/common/__init__.py | 1 | +21 | 新增导出 | 低 | 低（当前仅 1 行注释） |
| 2 | backend/core/middleware.py | 0（新建） | +57 | 新建文件 | 中 | 低 |
| 3 | backend/core/logging_config.py | 0（新建） | +73 | 新建文件 | 中 | 低 |
| 4 | backend/core/settings.py | 513 | +6 -40（净 -34） | MIDDLEWARE 注册 + LOGGING 收敛 | 中 | 中（LOGGING 42 行字面量 → 函数调用） |
| +| backend/tests/common/test_trace_id.py | 0（新建） | +220 | 新增测试（不算扩 scope，旧分支 R5 决策） | 低 | — |

> 说明：scope.new_files 只列了 middleware.py 与 logging_config.py。测试文件 `tests/common/
> test_trace_id.py` 为验证必需的配套新增，遵循旧分支 R5"新增测试不算扩 scope"决策——见第 7 节确认项。

## 4. 详细改动计划

### 文件 1: backend/apps/common/__init__.py

当前内容仅：`# 公共组件模块`（1 行）。**注意**：`apps.common` 下已存在 `middleware.py`
（TokenAuthMiddleware）、`exceptions.py`、`gateway_utils.py` 等子模块；本改动只动包的 `__init__.py`，
不影响这些子模块。

#### 改动 1.1（新增 contextvar 与 helper）
- 位置：文件全部替换
- 方案（对齐旧分支）：
  ```python
  """公共组件模块 — 基础设施工具集合。"""
  from __future__ import annotations
  import contextvars

  # trace_id 全局上下文变量（batch-04）
  trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

  def get_trace_id() -> str:
      return trace_id_var.get() or ""

  __all__ = ["trace_id_var", "get_trace_id"]
  ```
- 理由：`apps.common` 是全项目 import 路径最短、无重依赖的公共位置（旧分支 R1 决策）。
  仅依赖标准库 `contextvars`，无循环 import 风险。
- 预估：+21 -1

### 文件 2: backend/core/middleware.py（新建）

- 完全采用旧分支 `refactor/batch-04:backend/core/middleware.py`（57 行），要点：
  - `TRACE_HEADER = "HTTP_X_REQUEST_ID"`、`RESP_HEADER = "X-Request-ID"`
  - `sync_capable = True` + `async_capable = True`，`__init__` 用 `iscoroutinefunction(get_response)`
    判定同步/异步路径（`_scall` / `_acall`）
  - `_extract_or_generate()`：incoming header `strip()` 后长度 ≤128 则继承，否则 `uuid.uuid4().hex`
    （与 `chat_service.py:37 request_id = uuid.uuid4().hex` 生成方式一致）
  - set `trace_id_var` + `request.trace_id`，`return` 后写响应头 `X-Request-ID`
  - **不 reset**（见第 2 节）
- 理由：>128 字符防恶意超长 header；response 回写便于前后端关联。
- 预估：+57

### 文件 3: backend/core/logging_config.py（新建）

- 完全采用旧分支 `refactor/batch-04:backend/core/logging_config.py`（73 行），要点：
  - `TraceIdFilter`：从 `trace_id_var.get()` 取值，空则注入 `-`
  - `JSONFormatter`：输出合法 JSON；`_RESERVED` 白名单外的 `extra` 字段自动带入；
    非 JSON 可序列化值用 `repr()` 兜底（**永不丢日志**）；`exc_info` 格式化为字符串
  - `build_logging_dict(debug, log_level)`：返回带 `json`/`verbose`/`simple` formatter、
    对 uvicorn/django/apps 各 logger 挂 `trace_id` filter 的 dictConfig
- 理由：把 settings.py 里 42 行 LOGGING 字面量抽成函数，settings 更薄且格式统一。
- 预估：+73

### 文件 4: backend/core/settings.py

#### 改动 4.1（MIDDLEWARE 注册）
- 位置：第 57-69 行 `MIDDLEWARE` 列表
- 当前第一项为 `"corsheaders.middleware.CorsMiddleware"`（第 58 行）
- 方案：在**列表最顶端**（第 58 行之前）插入：
  ```python
  "core.middleware.TraceIdMiddleware",
  ```
- 理由：置于 CorsMiddleware 之上，使 CORS 拒绝的 OPTIONS 预检、以及任何早期短路响应也带
  trace_id（旧分支 R2 决策）。TokenAuthMiddleware（第 68 行，sync-only）不受影响——TraceIdMiddleware
  async_capable，Django ASGI 处理链会在其后自动插入 sync/async 适配器。
- 预估：+1

#### 改动 4.2（LOGGING 收敛）
- 位置：第 473-513 行整个 `LOGGING = {...}` 字面量（41 行）
- 方案：替换为：
  ```python
  from core.logging_config import build_logging_dict
  LOGGING = build_logging_dict(debug=DEBUG, log_level=os.getenv("DJANGO_LOG_LEVEL", "INFO"))
  ```
  （import 建议放文件顶部 import 区，或就近；`DEBUG` 定义于第 25 行、`os` 于第 8 行，均在 LOGGING 之前，无前向引用问题）
- 理由：三种日志格式统一为带 trace_id 的 JSON；settings 精简 ~34 行。
- 预估：+5 -40（净约 -34）
- **风险**：需保留当前 `apps.context.monitoring` DEBUG logger（旧分支 build_logging_dict 已含），
  避免监控埋点日志级别退化。

### 配套测试: backend/tests/common/test_trace_id.py（新建）

采用旧分支同名文件（~220 行，10+ 用例）：T1-T4 middleware header 继承/生成/超长丢弃/响应回写；
T5/T5b Filter 空与非空注入；T6/T6b/T6c JSONFormatter 合法 JSON/repr 兜底/exc_info；async 路径；
以及"trace_id 在 middleware 返回后仍存活"的回归保护用例（防止未来有人重新引入 reset）。

## 5. 逐步执行步骤与每步验证

- [ ] **步骤 1**：写 `apps/common/__init__.py`（改动 1.1）
  - 验证：`python -c "from apps.common import trace_id_var, get_trace_id"`（需先 activate venv）
- [ ] **步骤 2**：新建 `core/middleware.py`（文件 2）
  - 验证：`ruff check backend/core/middleware.py` + `python -c "from core.middleware import TraceIdMiddleware"`
- [ ] **步骤 3**：新建 `core/logging_config.py`（文件 3）
  - 验证：`ruff check backend/core/logging_config.py` + `python -c "from core.logging_config import build_logging_dict; build_logging_dict(True)"`
- [ ] **步骤 4**：改 `settings.py` MIDDLEWARE（改动 4.1）
  - 验证：`python manage.py check`（Django 系统检查，确认 middleware 可加载）
- [ ] **步骤 5**：改 `settings.py` LOGGING（改动 4.2）
  - 验证：`python manage.py check` + `python -c "import core.settings"` 无异常
- [ ] **步骤 6**：新建 `tests/common/test_trace_id.py`
  - 验证（局部，重活用 systemd-run 包裹）：
    `systemd-run --user --collect --pipe -- bash -lc 'source linchat/bin/activate && cd backend && pytest tests/common/test_trace_id.py -v'`
- [ ] **步骤 7**：lint 全量涉及文件
  - `ruff check backend/core/middleware.py backend/core/logging_config.py backend/core/settings.py backend/apps/common/__init__.py`

### 5.1 自动化验证（batch 定义要求）
- [ ] `pytest -k 'trace' -v`（新增测试全通过）
- [ ] 服务重启后：`curl -H 'X-Request-ID: test-123' http://localhost:8002/api/v1/health/ -i | grep -i x-request-id`
      （health 在 PUBLIC_PATHS，免 token；预期响应头回写 `X-Request-ID: test-123`）

### 5.2 手动验证（需安琳操作 — 见第 7 节）
- [ ] `./scripts/services.sh restart` 后检查 `/tmp/linchat-backend.log`（或实际日志路径）每行含 `trace_id` 字段
- [ ] 确认 uvicorn.access / django / apps 三类日志均为统一 JSON（`tail -f | jq .` 可解析）

### 5.3 回归验证
- [ ] `pytest backend/tests/common/ -v`（common 包无回归）
- [ ] `pytest backend/tests/ -q`（全量；旧分支基线 1603 passed，仅 1 个 perf 阈值 flaky 与本批无关）

## 6. 回滚策略

batch 定义：`git revert <commit>`；middleware 移除后日志退回旧格式，无数据影响（无 schema/无迁移）。

具体操作：
```bash
# 单 commit revert
git revert <commit-hash>

# 或整批 worktree 撤销
cd .. && git worktree remove linchat-batch-04 && git branch -D refactor/batch-04-v2
```
> 注意：不要复用旧分支名 `refactor/batch-04`（已存在且含旧 April 产物）；本轮建议用新分支名
> （如 `refactor/batch-04-v2`）避免与远端 `origin/refactor/batch-04` 冲突——见第 7 节确认项。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **分支命名冲突**：本地与远端已存在旧 `refactor/batch-04`（April，未合并）。本轮是复用同名
      分支覆盖，还是新开 `refactor/batch-04-v2`？（建议新开，避免污染旧产物历史）
- [ ] **测试文件算不算扩 scope**：scope.new_files 仅列 middleware.py 与 logging_config.py，未列
      `tests/common/test_trace_id.py`。旧分支 R5 决策"新增测试不算扩 scope"。请确认沿用。
- [ ] **settings.py 超 300 行硬限制**：settings.py 当前 513 行 > 300 行硬限制。本 batch 反而将其
      精简至 ~479 行（LOGGING 收敛）。是否需在本 batch 进一步拆分 settings.py？
      **建议不拆**：Django 惯例单文件 settings，拆分会大幅扩 scope 且触碰无关配置，风险 > 收益。
- [ ] **MIDDLEWARE 顶端注册（早于 CorsMiddleware）**：使 CORS 拒绝的 OPTIONS 也带 trace_id。
      确认接受此顺序（旧分支 R2 已采用并通过全量测试）。
- [ ] **手动日志验证需安琳操作**：`/tmp/linchat-backend.log` 中"每行含 trace_id、三格式统一 JSON"
      无法机器自动断言，需服务重启后人工 `tail | jq` 观察。请确认由安琳执行此步。
- [ ] **uvicorn 日志是否真正走 Django dictConfig（潜在风险）**：uvicorn 经 CLI 启动时有自身
      logging 初始化，Django 的 `LOGGING` 对 `uvicorn.access`/`uvicorn.error` 是否生效取决于启动
      顺序。旧分支验证通过，但当前 main 启动脚本可能不同。需在步骤 5.2 手动确认 uvicorn 访问日志
      确实变成了带 trace_id 的 JSON；若未生效，属**预期外**，需回到安琳讨论（不擅自改启动脚本/
      Docker 拓扑——do_not_touch）。

## 8. do_not_touch 合规自检

- 无 PostgreSQL schema / migration 改动 ✅
- 无 SSE 事件格式改动（仅 HTTP 请求头/日志）✅
- 无 SM3/SM4 改动 ✅
- 无 conversation_id/session_id 概念引入（trace_id 是每请求 header，非隔离粒度）✅
- 无 LangGraph/LangChain 版本改动 ✅
- 无 Docker 拓扑改动（不改启动命令；若 uvicorn 日志需调整启动参数 → 停下问安琳）✅
- 无前端栈改动 ✅
- 无 Gateway API 契约改动 ✅

## 9. 执行预算

- 预计 tool calls：~25（4 写 + 若干 lint/import 校验 + 局部 pytest）
- 预计 token：中等（有旧分支蓝本，无需大量探索）
- 预计时间：1 session（与 estimated_sessions=1 一致，未超 2 倍）

## 10. 小结

有旧分支完整蓝本兜底，风险主要落在：① settings.py LOGGING 收敛后 uvicorn 日志是否真正 JSON 化
（需人工验证）；② middleware 不 reset 的 contextvar 语义必须严格保留。无阻塞性红线冲突，待第 7 节
确认项获批后即可进入 executor 阶段。
