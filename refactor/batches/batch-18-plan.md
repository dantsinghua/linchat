# Batch batch-18 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估：5 文件 / ~200 行 / 1 session
> 依赖：batch-17 → STATUS: COMPLETED（已满足 ✅，HEAD=91dc219 为其 merge）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

在 batch-17 已把 `core/settings.py` 拆成 `core/settings/` 包的基础上，继续把
`__init__.py`（当前 435 行）中的 **LLM/安全/日志/第三方** 四个域抽到独立文件
（`llm.py` / `security.py` / `logging_conf.py` / `third_party.py`），用文件末尾
`from .xxx import *` 聚合，`__init__.py` 只保留核心 Django 配置。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/core/settings/__init__.py | 435 | -235（净减） | 抽出四域+新增4行聚合import | 中 | 高（435>300，本批后降至 ~200） |
| 2 | backend/core/settings/llm.py | 0 | +~135 | 新建 | 低 | — |
| 3 | backend/core/settings/security.py | 0 | +~55 | 新建 | 中（含 `not DEBUG`） | — |
| 4 | backend/core/settings/logging_conf.py | 0 | +~25 | 新建 | 中（import build_logging_dict） | — |
| 5 | backend/core/settings/third_party.py | 0 | +~35 | 新建 | 低 | — |

说明：MinIO 已在 batch-17 迁至 `media.py`，故 third_party 只含 Langfuse/Brave/HA。

## 3. 详细改动计划

### 拆分映射表（源：当前 __init__.py 行号 → 目标文件）

| 源行号 | 内容 | → 目标 | 依赖 base 变量？ |
|--------|------|--------|-----------------|
| 197-219 | REST_FRAMEWORK（含 throttle/rate_limit） | security.py | 否 |
| 222-226 | CORS_ALLOWED_ORIGINS / CORS_ALLOW_CREDENTIALS | security.py | 否 |
| 229-239 | SECURE_*/X_FRAME/Cookie（SESSION/CSRF _SECURE） | security.py | **是→DEBUG** |
| 242-244 | SM4_SECRET_KEY | security.py | 否 |
| 284-291 | AUTH_TOKEN_*/AUTH_CAPTCHA/AUTH_FAIL/AUTH_LOCK | security.py | 否 |
| 247-263 | LLM 超时/重试（LLM_CALL_TIMEOUT 等） | llm.py | 否 |
| 265-267 | MAX_MESSAGE_LENGTH | llm.py | 否 |
| 276-281 | LANGGRAPH_CHECKPOINT_* | llm.py | 否 |
| 331-357 | LLM_GATEWAY_*（URL/KEY/6 种超时/护栏） | llm.py | 否 |
| 359-373 | DOC_PARSE_* | llm.py | 否 |
| 375-386 | VIDEO_PREPROCESS/MULTIMODAL_* 运行参数 | llm.py | 否 |
| 388-394 | CONTEXT_HISTORY_ROUNDS/INFERENCE_TASK_TTL | llm.py | 否 |
| 396-415 | DOCUMENT_SUBAGENT/DOC_CHUNK/多模态 timeout | llm.py | 否 |
| 417-420 | SSE_HEARTBEAT_INTERVAL | llm.py | 否 |
| 270-273 | LANGFUSE_* | third_party.py | 否 |
| 313-316 | BRAVE_SEARCH_* | third_party.py | 否 |
| 318-329 | HA_*（HA_ENABLED 内部依赖 HA_URL/HA_TOKEN） | third_party.py | 否 |
| 423-428 | LOGGING = build_logging_dict(...) | logging_conf.py | **是→DEBUG** |

**留在 __init__.py（核心 Django，本批不动）**：header/imports/load_dotenv、
BASE_DIR/SECRET_KEY/DEBUG/ALLOWED_HOSTS(23-31)、INSTALLED_APPS/MIDDLEWARE/
ROOT_URLCONF/TEMPLATES/WSGI(34-95)、DATABASE(98-139)、Redis/CACHES(142-161)、
AUTH_PASSWORD_VALIDATORS(164-178)、i18n/Static/DEFAULT_AUTO_FIELD(181-194)、
**Memory(294-305)**、**Context Monitoring(308-311)**（这两域不在 batch-18 scope，
留待后续 memory 域批次，见 §7）。

### 域间变量依赖处理（核心陷阱，沿用 batch-17 经验）

`security.py` 与 `logging_conf.py` 引用 `DEBUG`。**不从 base import**（会构成
`__init__` ↔ 子模块循环）。改为在各自模块内用**下划线私有变量**独立重算：

```python
# security.py / logging_conf.py 顶部
import os
_DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"  # 与 base 同源同值
```

下划线前缀 → `from .security import *` **不会导出 `_DEBUG`**，因此不覆盖 base 第 29
行的 `DEBUG`（即使值相同也保持语义清晰）。这直接命中"__all__/下划线变量在
import * 下可见性"陷阱。security.py 内 `SESSION_COOKIE_SECURE = not _DEBUG` 等改用
`_DEBUG`。

### 新文件骨架（示例：security.py）

```python
"""安全与 API 访问策略配置（REST_FRAMEWORK/CORS/Cookie/SM4/Auth Token）。

batch-18 从 core/settings/__init__.py 迁出。各值用 os.getenv 独立取值；
DEBUG 用模块内私有 _DEBUG 重算，避免与 base 循环 import。
"""
import os

_DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"

REST_FRAMEWORK = { ... }          # 原 197-219，含 throttle rate_limit
CORS_ALLOWED_ORIGINS = ...        # 原 222-226
CORS_ALLOW_CREDENTIALS = True
SECURE_BROWSER_XSS_FILTER = True  # 原 229-239
# ...
SESSION_COOKIE_SECURE = not _DEBUG
CSRF_COOKIE_SECURE = not _DEBUG
SM4_SECRET_KEY = os.getenv("SM4_SECRET_KEY", "default-sm4-key-16")  # 原 242-244
AUTH_TOKEN_IDLE_TTL = 3600        # 原 284-291
# ...
```

`logging_conf.py`：`from core.logging_config import build_logging_dict`（该模块只
`from apps.common import trace_id_var`，apps.common 无 settings import → **无循环**，
已由 batch-17 第 424 行在同一时机验证过），`LOGGING = build_logging_dict(debug=_DEBUG, log_level=os.getenv("DJANGO_LOG_LEVEL","INFO"))`。

`llm.py` / `third_party.py`：纯 `os.getenv`，无 base 依赖，结构同 batch-17 的
celery_conf.py（不定义 `__all__`，与既有三域保持一致）。

### __init__.py 末尾聚合 import 顺序（新增 4 行，追加在现有三行后）

```python
# ============ 域配置聚合（batch-17 三域 + batch-18 四域）============
from .celery_conf import *      # noqa: E402,F401,F403  （batch-17）
from .media import *            # noqa: E402,F401,F403
from .voice import *            # noqa: E402,F401,F403
from .security import *         # noqa: E402,F401,F403  （batch-18）
from .llm import *              # noqa: E402,F401,F403
from .third_party import *      # noqa: E402,F401,F403
from .logging_conf import *     # noqa: E402,F401,F403  ← 置于最后
```

**顺序关键点**：所有域文件互不依赖（各自 os.getenv），顺序在功能上无关；
但把 `logging_conf` 放**最后**是刻意的文档化约定——它是"消费型"配置（构建
LOGGING dict），放末尾表达"最终态"。同时删除 __init__.py 原第 422-428 行的
`from core.logging_config import build_logging_dict` + LOGGING 赋值（已迁走）。

## 4. 调查步骤（fix 类专用）

本批为 refactor，非 fix，无根因调查。已完成的只读核实：
- [x] batch-17 已 COMPLETED（progress 末尾 STATUS: COMPLETED）
- [x] __init__.py 现状 435 行，四域行号已定位（见 §3 映射表）
- [x] core.logging_config 不 import settings/django.conf → logging_conf 无循环
- [x] apps.common 仅 contextvars/uuid，无 settings 依赖 → import 链安全
- [x] 全仓无 `from core.settings import X`，均走 `django.conf.settings` → import * 安全
- [x] conftest.py 用 Django settings 覆盖机制重写 REST_FRAMEWORK throttle，与文件位置无关 → 移到 security.py 不影响测试

## 5. 验证计划

### 5.1 自动化验证（前置：`source linchat/bin/activate`，cwd=backend）
- [ ] `python manage.py check`（期望：System check identified no issues）
- [ ] diffsettings 前后一致（黄金校验，同 batch-17）：
      ```bash
      # 拆分前（当前 HEAD）先存基线
      python manage.py diffsettings --all > /tmp/ds_before.txt
      # 拆分后
      python manage.py diffsettings --all > /tmp/ds_after.txt
      diff /tmp/ds_before.txt /tmp/ds_after.txt   # 期望：无差异
      ```
- [ ] `ruff check core/settings/ && black --check core/settings/ && isort --check core/settings/`
- [ ] 属性 smoke：`python -c "from django.conf import settings; print(settings.LLM_GATEWAY_URL, settings.SM4_SECRET_KEY, settings.LANGFUSE_HOST, settings.HA_ENABLED, bool(settings.LOGGING), settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])"`

### 5.2 手动验证
- [ ] 无（配置纯迁移，无行为变更）

### 5.3 性能验证
- [ ] 无（P2，非性能批次）

### 5.4 回归验证
- [ ] 全量：`pytest backend/tests/ -q`（batch-17 基线 1672 passed / 9 skipped / 0 failed）
- [ ] 用 systemd-run 包裹避免 scope 耗尽（沿用 batch-17 做法）

> 注：plan JSON validation.automated 含 `./scripts/services.sh restart`，但本任务
> **严禁停止/重启服务**（见任务约束）。改为仅 `manage.py check` + diffsettings +
> pytest 静态验证；服务重启留给安琳在 review 后手动决定（见 §7）。

## 6. 回滚策略

`git revert <commit>`（rollback_strategy 原文）。四个新文件 + __init__.py 改动应在
**同一 commit**，revert 单 commit 即完全恢复 batch-17 后状态。或 worktree 级：
```bash
git worktree remove ../linchat-batch-18 && git branch -D refactor/batch-18
```

## 7. ⚠️ 需要安琳确认的事项

- [ ] **"~100 行"目标不可达（scope 内）**：DATABASE 解析(42行)+Redis/CACHES(20)+
      INSTALLED_APPS/MIDDLEWARE/TEMPLATES(62) 共 ~124 行核心 Django 配置，在
      batch-18 的四域 scope（llm/security/logging/third_party）内无处可去。本批后
      __init__.py 现实落点 **~200 行**（435→~200）。要到 ~100 需再拆
      database.py/apps.py（**超出本批 scope**）。是否接受 ~200 行为本批终点，把
      DB/apps 拆分留到后续批次？
- [ ] **Memory(294-305)+Context Monitoring(308-311) 归属**：不在四域命名内，计划
      **留在 base**（batch-17 notes 已预告"后续 batch 拆 memory"）。确认留在 base、
      不塞进 llm.py？
- [ ] **REST_FRAMEWORK 归入 security.py**：因其含 throttle rate_limit（契合 batch
      描述的 "rate_limit"）。若你认为它更该留 base 作核心 DRF 配置，请指示。
- [ ] **服务重启**：plan JSON 的 validation 含 `services.sh restart`，但本任务硬约束
      "严禁停止/重启服务"。计划仅做静态验证（check/diffsettings/pytest）。重启由你
      在 review 后手动执行确认生效。

以上为需确认项；技术实现无阻塞，映射与 import 设计已就绪。

## 8. 执行预算

- 预计 tool calls：~20（4 次 Write 新文件 + 1 次 Edit __init__ + 6~8 次验证）
- 预计 token：~40k
- 预计完成时间：单 session（与 estimated_sessions=1 一致）
- 未超 2× 预算，无需拆分。
