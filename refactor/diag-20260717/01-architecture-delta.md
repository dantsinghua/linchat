# LinChat 架构增量分析（Delta / Rediagnosis R2）

> 生成时间：2026-07-17
> 基线：`501cd42`（2026-04-17，四月计划）→ HEAD（2026-07-17，108 commits）
> 范围：**仅变更模块**（`git diff --name-only 501cd42..HEAD -- backend/`）+ 非 backend 新代码
> 先验：`docs/legacy-and-debts.md`（安琳 reviewed）

## 执行摘要

- 变更 backend 文件：91（含 40+ 测试）；今日 refactor loop 集中产出
- 分层违规（新/存量）：**8 处**（voice services 直接 ORM 绕过 repositories）
- 超 300 行文件：**2 个**（较四月的上帝模块显著收敛）
- 新增跨 app 耦合边：**2 条**（voice→chat、voice→graph 深化）
- 循环依赖：0（靠函数内 lazy import 规避）
- 最严重 Top 3：① voice services 层 ORM 违规不一致；② voice→chat 内部结构耦合（ambient_light 新增）；③ settings 包 docstring 与实际拆分漂移

---

## 1. 新模块与既有架构一致性

### 1.1 ✓ 良好：Consumer Mixin 化（batch-19-21）
四月遗留的上帝 consumer 已按安琳决定拆分为 Mixin 架构：
```
VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer)
```
- `consumers.py:26` 组装三个 Mixin（consumer_session/events/inference.py）
- `protocols.py:VoiceConsumerProtocol`（batch-19-21 新，83 行）用 `typing.Protocol` 为 Mixin 声明契约，三个 consumer_* 均引用 — **类型边界清晰，是本轮亮点**
- ✓ 拆分后单文件均 ≤303 行（session 303 / events 218 / inference 114）

### 1.2 ✓ 良好：settings.py → settings/ 包（batch-17/18）
- 单文件 `core/settings.py` 已删除，改为 `core/settings/` 包
- `__init__.py`（base，230 行）末尾聚合 `from .{celery_conf,media,voice,security,llm,third_party,logging_conf} import *`
- ✓ 域拆分合理（voice 136 / llm 129 / security 69 / media 39）
- ⚠ **文档漂移**：`__init__.py:8-10` docstring 称 "base 保留 …LLM/安全… 配置"，但 LLM/security 已实际迁至 `llm.py`/`security.py`（base 中已无 `LLM_*` 定义）。星号导入在文件末尾覆盖，行为正确但注释误导，易致后人改错文件。

### 1.3 ✓ 良好：core/redis.py 池化（batch-11）
- `_get_pool()` 用 `aioredis.BlockingConnectionPool`（连接满时阻塞而非报错），ASGI 单事件循环进程内复用
- 同时提供 `RedisClient`(async) + `SyncRedisClient`；被 10+ 文件采用（voice/users/chat/common）
- ✓ 与"Redis 是副本、PostgreSQL 唯一可信源"红线不冲突

### 1.4 ✓ 良好：graph inference/types 迁移（batch-16）
- `StreamChunk` 全库唯一定义于 `graph/services/types.py:32`；`chat/services/types.py` 仅 re-export（11 行兼容层）— **无重复实现**
- chat→graph 依赖均为已知兼容层（安琳清单在案），非新增债

---

## 2. 分层边界违规（红线 5：views→services→repositories）

| # | 文件:行 | 类型 | 说明 |
|---|---------|------|------|
| 1 | `voice/services/voice_persist_service.py:82,86,92,129,130,139` | service 直接 ORM | `Message.objects.*` / `MediaAttachment.objects.create`，绕过 repositories |
| 2 | `voice/services/voice_pipeline.py:248` | service 直接 ORM | `Message.objects.filter(...).update` |
| 3 | `voice/services/speaker_service.py:160` | service 直接 ORM | `Message.objects.filter(...).update` |

- ⚠ **不一致**：同为 voice service，`ambient_light_service.py:25` 正确使用 `message_repo`，而上述 3 文件直连 ORM。仓储层使用无统一约定，重构应统一收敛到 `message_repo`。
- ✓ 未发现 service 层返回 HTTP 对象 / SSE / consumer 直接 ORM（这些边界干净）

---

## 3. 新增模块依赖耦合

```mermaid
graph LR
  voice --> chat
  voice --> graph
  voice --> common
  chat -. compat re-export .-> graph
  graph --> context
  graph --> models
```

- ✓ **新增边 voice→chat**（batch-08 新 `ambient_light_service.py`）：直接 import `chat.models.Message`、`chat.repositories.message_repo`、`chat.services.types.StreamChunk`（3 处）。voice 穿透进 chat 的**模型/仓储/类型**三层内部，耦合偏深。
- ✓ **voice→graph 深化**（`voice_pipeline.py` import graph ×2）：语音链路复用 Agent 推理，合理但加深跨 app 依赖。
- ✓ 循环依赖规避靠 **函数内 lazy import**（`consumers.py` 方法内 `from ...tts_router import TTSRouter`；`chat_service.py:33,57` 方法内 import AgentService）— 能跑但掩盖真实依赖图，静态分析失真。

---

## 4. 超 300 行文件清单（变更集内）

| 文件 | 行数 | 近3月 commits | 备注 |
|------|------|--------------|------|
| `voice/services/voice_pipeline.py` | 309 | 6 | ⚠ 变更最频繁 + 唯一超 300 的 service，含 ORM 违规，热点 |
| `voice/consumer_session.py` | 303 | 3 | Mixin 拆分后仍最大，可再分 |

> 其余变更文件均 ≤266 行。相比四月上帝模块（voice 多个 >400 行），本轮**体积治理有效**。

---

## 5. 非 backend 新代码（post-April）

- `refactor/loop/*.py|sh`（loopctl/validate_full/perf_bench/trigger_voice_e2e/start-loop）：重构自动化工装，独立于 backend，无架构侵入。
- `scripts/respeaker_bridge/`（4 月，硬件串口桥）：隔离于 backend 进程，通过 WS 对接 voice consumer，边界清晰。
- 二者均不影响后端分层结论。

---

## 6. Open Questions（需安琳确认）

1. voice service 层 ORM 违规（persist/pipeline/speaker）是否纳入本轮统一收敛到 `message_repo`？还是暂缓（voice 属"最活跃"高风险区）？
2. `settings/__init__.py` docstring 漂移是否本轮顺手修正（低风险纯注释）？
3. voice→chat 三层穿透（ambient_light）是否可接受，还是应经由 chat 的 service 门面隔离？
4. 函数内 lazy import 规避循环依赖，是否有意保留（作为架构约束标记）？

---
*Delta 结论：本轮 refactor loop 方向正确——上帝模块拆分、Mixin/Protocol 契约化、settings 分域、redis 池化均落地且一致性良好。残留主要问题集中在 voice service 层仓储使用不一致 + voice→chat 深耦合，建议交 refactor-planner 作为下一批候选。*
