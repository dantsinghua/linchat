# Batch batch-17 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估：5 文件 / ~250 行 / 1 session
> 依赖：depends_on = []（无依赖，直接可执行）
> SLO 影响：无（blocks_slo=null）
> 核实基线：main HEAD=ccfc7e3；settings.py 实测 490 行 / 122 处 getenv（计划书原写 513/117，已按现状修正）

## 1. 任务理解（一句话）

把单文件 `backend/core/settings.py` 拆成 `backend/core/settings/` 包，voice/media/celery
三域各自独立成文件，`__init__.py` 保留 Django 基础配置并 `from .xxx import *` 聚合，
保持 `DJANGO_SETTINGS_MODULE=core.settings` 模块路径与所有现有 `django.conf.settings.X`
访问方式 100% 不变。**只拆分文件组织，不改任何配置值。**

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/core/settings.py | 490 | 删除（转为包）| 删文件 | 中 | 高（490行 > 300 硬限制，本 batch 即拆分） |
| 2 | backend/core/settings/__init__.py | 新增 | ~320 | 新增（base + 聚合 import）| 中 | — |
| 3 | backend/core/settings/voice.py | 新增 | ~72 | 新增（迁移 CHANNEL_LAYERS+VOICE_*）| 低 | — |
| 4 | backend/core/settings/media.py | 新增 | ~24 | 新增（迁移 MinIO+上传+媒体限制）| 低 | — |
| 5 | backend/core/settings/celery_conf.py | 新增 | ~15 | 新增（迁移 CELERY_*）| 低 | — |

> 注：git 中 `settings.py`→`settings/` 的转换必须先 `git rm settings.py` 再新建目录，否则
> Python 会同时看到 `settings.py` 与 `settings/`（package）造成导入歧义。

## 3. 详细改动计划

### 拆分映射表（源行号 → 目标文件）

| 源行号（settings.py）| 配置项 | 目标文件 |
|---|---|---|
| 1-386 中的基础/DB/Redis/REST/CORS/安全/LLM/Langfuse/Memory/Context/Brave/HA/多模态/DocParse | 除 voice/media/celery 外全部 | `__init__.py`（base）|
| 290-302 | `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`/`CELERY_ACCEPT_CONTENT`/`CELERY_TASK_SERIALIZER`/`CELERY_RESULT_SERIALIZER`/`CELERY_TIMEZONE`/`CELERY_ENABLE_UTC` | `celery_conf.py` |
| 342-350 | `MINIO_*`（6 项，含 `MINIO_AUDIO_BUCKET`）| `media.py` |
| 384-386 | `FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE` | `media.py` |
| 388-395 | `MEDIA_MAX_*`（7 项）| `media.py` |
| 400-411 | `CHANNEL_LAYERS`（语音 WebSocket）| `voice.py` |
| 413-468 | `VOICE_ASR_*`/`VOICE_TTS_*`/`VOICE_SESSION_*`/`VOICE_SPEAKER_*`/`VOICE_VAD_*`/`VOICE_AMBIENT_*`/`VOICE_DECISION_*`/`VOICE_DIARIZE_*`/`VOICE_DEFAULT_WAKE_WORDS`/`VOICE_MAX_*`/`VOICE_IDLE_TIMEOUT`/`VOICE_STT_TIMEOUT`（约 40 项）| `voice.py` |

**留在 base（__init__.py）不动的易混项**（明确不迁，避免 scope 蔓延）：
- 374-375 `VIDEO_PREPROCESS_WIDTH`、377-379 `MULTIMODAL_*`、366-372 `DOC_PARSE_*`、
  470-481 `DOCUMENT_SUBAGENT_*`/`DOC_*`/`MULTIMODAL_SUBAGENT_*`/`AGENT_MULTIMODAL_TIMEOUT`
  → 属「多模态/文档网关」域，**本 batch 不动**（留待后续批次）。
- 483-490 `SSE_HEARTBEAT_INTERVAL`、`LOGGING`（build_logging_dict）→ 留 base。

### 文件 2: backend/core/settings/__init__.py（base + 聚合）

#### 改动 2.1 —— ⚠️ BASE_DIR 层级修正（trap ①，最关键）
- 源位置：settings.py:17 `BASE_DIR = Path(__file__).resolve().parent.parent`
- 现状语义：`settings.py`→parent=`core`→parent=`backend`，BASE_DIR=backend ✅
- 变包后：`settings/__init__.py`→parent=`settings`→parent=`core`→parent=`backend`
- 改动方案（必须加一层）：
  ```python
  # 文件从 core/settings.py 变为 core/settings/__init__.py，路径深一层
  BASE_DIR = Path(__file__).resolve().parent.parent.parent
  ```
- 理由：不修正会导致 `STATIC_ROOT`（:186）等所有 BASE_DIR 派生路径错位一层。
- 验证锚点：`diffsettings` 中 `BASE_DIR` 前后必须完全一致。

#### 改动 2.2 —— base 主体
- 把 settings.py 中「非 voice/media/celery」的所有内容原样保留在 `__init__.py`，
  保持相对顺序不变（`import re` @103、`from core.logging_config import ...` @488 均保留）。
- 删除已迁走的三域代码块（290-302、342-350、384-395、400-468）。

#### 改动 2.3 —— 末尾追加聚合 import（放文件最末，LOGGING 之后）
  ```python
  # ============ 域配置聚合（batch-17：settings 包拆分）============
  # 各域文件用 os.getenv 独立取值，不依赖 base 内变量，import 顺序无关。
  from .celery_conf import *  # noqa: E402,F401,F403
  from .media import *        # noqa: E402,F401,F403
  from .voice import *        # noqa: E402,F401,F403
  ```
- 理由：`from .xxx import *` 把三域符号注入 `core.settings` 命名空间，
  使 `settings.VOICE_AMBIENT_AGGREGATE_TIMEOUT` 等访问方式零改动。

### 文件 3: backend/core/settings/voice.py
  ```python
  """语音交互配置（010-voice-agent-pipeline ~ 017-ambient-speaker-id）。
  从 core/settings.py 迁出（batch-17）。各值用 os.getenv 独立取值。"""
  import json
  import os
  # <原 settings.py:400-468 内容原样粘贴：CHANNEL_LAYERS + 所有 VOICE_*>
  ```
- ⚠️ 必须带 `import json`（`VOICE_TTS_COMFORT_TEXTS` @423 用 `json.loads`）与 `import os`。

### 文件 4: backend/core/settings/media.py
  ```python
  """媒体存储与上传限制配置（MinIO + 文件上传 + 媒体大小）。batch-17 迁出。"""
  import os
  # <原 settings.py:342-350 MINIO_* + 384-386 FILE/DATA_UPLOAD + 388-395 MEDIA_MAX_*>
  ```

### 文件 5: backend/core/settings/celery_conf.py
  ```python
  """Celery 配置。batch-17 迁出。命名 celery_conf 避免与 core/celery.py 冲突。"""
  import os
  # <原 settings.py:290-302 CELERY_*>
  ```
- ⚠️ 文件名用 `celery_conf.py`（非 `celery.py`），防止与 `backend/core/celery.py` 混淆。

## 4. 调查步骤（已完成的核实结论）

- [x] **trap ① BASE_DIR**：确认现状 `parent.parent`=backend；变包后需 `parent.parent.parent`。已纳入改动 2.1。
- [x] **trap ② 域间变量依赖**：逐块核实 voice/media/celery 三域**均只用 os.getenv/字面量**，
      celery 的 broker 默认值是硬编码 `redis://...6379/2`（不引用 base 的 `REDIS_URL`），
      **无跨域变量依赖** → import 顺序无关，各域自带 `import os`（voice 另需 `import json`）。
- [x] **trap ③ 直接 import**：`rg "from core.settings import"` 全库 **0 处**；
      全部走 `django.conf.settings` 或 `DJANGO_SETTINGS_MODULE=core.settings` 字符串 → 包路径兼容。
- [x] **trap ④ 启动引用**：manage.py:9 / asgi.py:15 / wsgi.py:10 / celery.py:17 / conftest.py:9 /
      pytest.ini:2 / scripts/validate-batch-05.sh 全部用 `core.settings` 字符串，
      Python 对包（`settings/__init__.py`）与模块（`settings.py`）解析等价 → **无需改动这些文件**。

## 5. 验证计划

### 5.1 自动化验证（core check + diffsettings 前后一致——启动核心必做）
- [ ] **拆分前**建基线（在干净工作树先做）：
      `cd backend && source ../linchat/bin/activate && python manage.py diffsettings > /tmp/diffsettings_before.txt`
- [ ] **拆分后**对比：
      `cd backend && python manage.py diffsettings > /tmp/diffsettings_after.txt`
      `diff /tmp/diffsettings_before.txt /tmp/diffsettings_after.txt`
      —— **必须无差异**（尤其 BASE_DIR / STATIC_ROOT / 三域全部键）。
- [ ] `cd backend && python manage.py check` 无 error。
- [ ] `python -c 'from django.conf import settings; import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE","core.settings"); print(settings.VOICE_AMBIENT_AGGREGATE_TIMEOUT, settings.MINIO_ENDPOINT, settings.CELERY_BROKER_URL, settings.BASE_DIR)'`
- [ ] `ruff check backend/core/settings/`
- [ ] `pytest backend/tests/ -q`（全量回归）

### 5.2 手动验证步骤
- [ ] 确认 `git status` 中 `settings.py` 为 deleted、`settings/` 4 文件为 new。
- [ ] 目视核对三域文件行数总和 ≈ 原 290-468/342-395 区块，无遗漏无重复。
- [ ] **不执行** `./scripts/services.sh`（本任务禁止启停服务）；启动验证由安琳在 review 后手动做。

### 5.3 性能验证
- 不适用（P2，无 metrics）。

### 5.4 回归验证
- [ ] `pytest backend/tests/ -q` 通过（配置加载影响全局，全量即可）。

## 6. 回滚策略

配置文件拆分，回滚简单：
```bash
git revert <commit-hash>          # 单 commit revert，恢复单文件 settings.py
# 或本地未提交时：
git checkout -- backend/core/settings.py
rm -rf backend/core/settings/
```

## 7. ⚠️ 需要安琳确认的事项

- [ ] `backend/core/settings.py` 当前 490 行，超过 300 行硬限制。本 batch 目标即拆分，
      但**仅拆 voice/media/celery 三域**（scope 明确），拆后 base（__init__.py）仍约 320 行、
      **仍 > 300**。是否接受本批次后 base 暂时仍超限（多模态/gateway 域留待后续批次）？
      —— 按 04-refactor-plan.json 描述「第一批」，倾向接受；请确认不在本批强行扩到再拆 base。
- [ ] `backend/core/CLAUDE.md` 与 `backend/CLAUDE.md` 中有「settings.py（512 行）」等描述性文字，
      拆分后描述失准。是否在本 batch 顺带更新这两处 doc（属 docs，不算业务代码），
      还是留到 doc 统一批次？—— 默认**不动**，等你决定。

其余 trap ①②③④ 均已核实闭环，**无其他阻塞**，可进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：~20（1 次 git rm + 4 次 Write + 若干验证 Bash）。
- 预计 token：中等（settings.py 已读入上下文）。
- 预计完成：1 session，符合 estimated_sessions=1。
