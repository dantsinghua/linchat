# LinChat 架构增量分析（Delta / Rediagnosis R2 — 第二轮增量）

> 生成时间：2026-07-17
> 基线：tag `diag-20260717` → HEAD（batch-29~36 产物）
> 范围：`git diff --stat diag-20260717..HEAD -- backend/`（39 文件，含 22 测试）
> 先验：`refactor/diag-20260717/01-architecture-delta.md`（R1）+ `docs/legacy-and-debts.md`

## 执行摘要

- 变更 backend 文件：39（22 个测试，+1077/-170 行）
- **R1 头号债已清偿**：voice services 层 ORM 直连（R1 列 3 处违规）→ **batch-33 全部收敛至 message_repo / media_attachment_repo，voice/services 现零 `.objects.`**
- 新增架构债：**2 项轻量 + 1 项配置层 backlog**（均非阻塞）
- 循环依赖：0；新增跨 app 耦合边：0（voice→chat 深度未增，反而随 repo 收敛而减轻）
- 建议新 batch：**1 个可选**（voice_pipeline 拆分，热点高风险区，非紧急）
- 最高优先级：voice_pipeline.py 326 行 > 300 硬限（P2）

---

## 1. R1 遗留项闭环情况

### 1.1 ✓ 已清偿：voice services ORM 违规（R1 §2 头号债）
batch-33 将 R1 标记的 3 处 service 直连 ORM 全部收敛至仓储层，证据：

| R1 违规点 | 现状（收敛后调用点） |
|-----------|---------------------|
| `voice_persist_service.py` `Message.objects.*` / `MediaAttachment.objects.create` | `message_repo.get_by_request_id_sync/set_voice_flag_sync/create` + `media_attachment_repo`（:83,85,91,93,100,107,125） |
| `voice_pipeline.py:248` `Message.objects.filter().update` | `message_repo.update_content_by_request_id`（:266） |
| `speaker_service.py:160` `Message.objects.filter().update` | `message_repo.reassign_speaker_messages`（:158） |

- 校验命令 `rg "\.objects\.(get\|filter\|...)" backend/apps/voice/services` → **0 命中**；`(Message\|MediaAttachment)\.objects` → **0 命中**。
- 仓储层补全：`chat/repositories.py` +47 行、`media/repositories.py` +22 行，新增上述 sync/async 方法。R1 §2 "仓储使用不一致" 问题消除，voice 全域统一走 repo。**结论：R1 头号债完整收敛，无残留。**

### 1.2 ⚠ 未闭环（体积）：voice_pipeline.py 仍 >300 且继续增长
- R1 记 309 行 → 现 **326 行**（batch-32 自适应 flush + batch-33 repo 收敛叠加），仍超 300 硬限，仍为最大 voice service。
- `consumer_session.py` 310 行同样 >300（R1 记 303，微增）。
- 变更集内其余文件均 ≤278 行。

---

## 2. 本轮新增架构债

### 2.1 ⚠ chat<->graph 兼容 shim（batch-34/35）— 分两类，结论不同

**(a) `chat/services/generation.py`（11 行 re-export）— 载荷型 shim，暂留合理**
- batch-35 已把 generation 信号注册表真源迁至 `graph/services/generation.py`（消反向耦合，方向正确 ✓）。
- chat 侧 shim **仍被运行时 + 测试双重依赖**：`chat_service.py:10` 运行时 import；`tests/chat/test_services.py`（6 处）、`test_concurrency.py`、`test_inference_cancel.py`、`performance/test_smoke.py` 共 ~10 处 `from apps.chat.services[.generation] import _active_generations/register_generation`（即注释所称"字符串 patch 契约"）。
- 债类型：兼容层/测试契约耦合。**判断：非死代码，删除需连带改 ~7 测试文件，风险>收益。建议随后续 chat 测试整理一并清理，不单独成 batch。**
- 次要味道：`map_llm_exception` 三跳链 `chat.generation → graph.generation → common.exceptions`（graph/generation.py:5 自身也是 re-export），可在 (b) 清理时顺带压平。

**(b) `chat/services/__init__.py:20-24` 兼容 re-export — 死代码，可删**
- `ContextService / InferenceService / MediaService / MinioService / DocumentParseService`（及其单例）经 chat.services 的 re-export，**全库零消费者**（`rg "chat\.services" | rg 这些符号` 仅命中 `common/CLAUDE.md` 文档，无代码）。
- 债类型：死兼容 re-export（误导性 API 表面）。**建议：可纳入一个"死 shim 清理"小 batch（与 batch-34 同性质），优先级 P3，风险极低（删除后需 pytest 全绿确认无隐式 import）。**

### 2.2 ⚠ dark-launch flag 累积（batch-30/31/32）— 配置层 backlog，非架构债
- `core/settings/voice.py` 现有 **6 个灰度开关**，其中 3 个本轮新增且默认 `false`：
  `VOICE_DECISION_SHORTCIRCUIT_ENABLED`(:142) / `VOICE_HA_PARALLEL_TTS_ENABLED`(:65) / `VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED`(:110)；
  另有 `VOICE_TTS_PRECONNECT_ENABLED`(:59, 默认 false) 亦长期未转正。
- 每个 flag 都**保留一条旧回退代码路径**（注释均写"旧路径代码保留，运行时可回滚"）。累积后 voice 关键路径存在多条并行 dead-on-default 分支，增加认知与测试负担。
- 债类型：配置/双路径累积。**判断：这是"待压测转正"的 backlog，不是可静态修的架构债；需安琳按压测结果决定 flag 转正（置 true / 删旧路径）时点。建议单独建一个"flag 转正 + 死路径清理"跟踪项，勿在 R2 直接动（涉及运行时行为，且 SSE/语音关键路径属高风险）。**

---

## 3. 未发现的问题（确认干净）

- ✓ 无新增循环依赖；无新增 service→HTTP/SSE 反向穿透；consumer 层无直连 ORM。
- ✓ 无新增跨 app 耦合边。voice→chat 仅剩 `Message` 模型 + `StreamChunk` 类型 import（`voice_persist_service.py:13`、`ambient_light_service.py:24,26`）——此为 R1 已记存量债，本轮**未加深**（数据访问已全部走 repo，穿透面收窄）。
- ✓ media batch-36（派发前 rowcount gate + worker not-found 降级）为纯健壮性修复，无分层影响。
- ✓ 新增 repo 方法均落在正确层（chat/media repositories.py），分层方向正确。

---

## 4. 建议与优先级

| # | 发现 | 债类型 | 是否成 batch | 优先级 | 风险 |
|---|------|--------|-------------|--------|------|
| 1 | voice_pipeline.py 326 行 >300（+consumer_session 310） | 体积/上帝雏形 | **可选独立 batch**（拆聚合/持久/推理编排） | P2 | 高（voice 最活跃区，纯结构重排+测试护栏，勿改行为） |
| 2 | chat/services/__init__ 死兼容 re-export（5 符号） | 死 shim | 可并入"死 shim 清理"小 batch | P3 | 低（删后 pytest 全绿即可） |
| 3 | dark-launch flag 累积（3 新+1 旧默认 false） | 配置/双路径 backlog | **不成 R2 batch**，建跟踪项待压测转正 | P3 | 中（触运行时/语音关键路径，需安琳定时点） |
| 4 | chat.services.generation 载荷 shim + map_llm_exception 三跳 | 兼容层/测试契约 | 随 chat 测试整理顺带，不单独成 batch | P3 | 中（牵动 ~7 测试文件的 patch 契约） |

---

## 5. Open Questions（需安琳确认）

1. voice_pipeline（326）+ consumer_session（310）是否本轮就做拆分 batch？考虑 voice 属"最活跃/高风险"区，是否宁可再攒一轮稳定后再动？
2. chat/services/__init__ 的 5 个死兼容 re-export 是否可直接删（P3，仅需全量测试护栏）？
3. 4 个默认 false 的 dark-launch flag（PRECONNECT/HA_PARALLEL/ADAPTIVE_FLUSH/SHORTCIRCUIT）压测结论如何？可转正并删旧路径的有哪些？（此项非我可静态判定）

---
*R2 Delta 结论：batch-29~36 收敛质量高——R1 头号债（voice ORM 违规）已完整清偿，无新增循环依赖/跨层穿透。新增债均为轻量（死 shim P3 + flag backlog P3），唯一 P2 是 voice_pipeline 体积未治理且继续增长。建议下一批优先候选 = voice_pipeline 拆分（可选），其余归入低优先清理/跟踪。交 refactor-planner 汇总。*
