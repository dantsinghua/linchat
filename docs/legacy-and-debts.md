# LinChat 历史包袱与已知痛点

> 这份文档是**安琳**对项目真实状态的主观记录，用于指导重构决策。
> Claude 在做 Phase 1 重构分析时**必须读取此文档**——它包含代码无法自动发现的上下文。
> ✓ = 客观观察（自动扫描）  安琳 = 安琳主观补充  待验证 = Phase 1 分析核实

---

## 一、历史迁移痕迹

### Agent 框架迁移

- ✓ LangChain/LangGraph 依赖隔离良好，全部在 `apps/graph/` 和 `tests/` 中，未扩散到其他 app
- ✓ `apps/graph/prompts.py`（8 行）是兼容层 → `apps/context`
- ✓ `apps/graph/services/agent_helpers.py`（63 行）是兼容层 → `helpers/` 子包
- ✓ `agent_service.py`（246 行，11 次修改）`execute()` 单方法 ~150 行编排 7 步流程，已拆分 helpers/ 但仍较臃肿

### Context 记忆系统

- ✓ `apps/context/tokenizer.py`（4 行）兼容层 → `apps/common/tokenizer`
- 待验证：混合搜索权重 0.7/0.3 是否经过验证

### 语音管道

- ✓ voice 模块总计 **2,794 行**，经历 **7 个特性迭代**（009→010→013→014→015→016→017）
- ✓ 3-Mixin 架构（`consumers.py` + 3 mixin）共 750 行，通过 `self._*` 共享状态，无接口约束
- ✓ 2 处 `[DEPRECATED] diarize` 注释代码待清理（`consumers.py:63`, `consumer_session.py:77`）
- ✓ Git Top 15 中 voice 占 **6 席**，是最活跃且最复杂的子系统
- **安琳决定**：本轮重构整理 Mixin 架构

### 文档 RAG

- ✓ `DocumentParseService`（`document.py:189`）兼容委托 + 核心逻辑混合，职责不够单一
- **安琳决定**：本轮清理兼容委托

### 兼容层 shim 文件（✓ 共 12 处）

重构后保留的 re-export 文件，实际逻辑已迁移到新位置：

| 兼容层文件 | 原模块 → 新模块 |
|-----------|-----------------|
| `chat/sse.py` | chat → common.sse |
| `chat/tasks.py` | chat → media.tasks |
| `chat/services/document_parse_service.py` | chat → media.services.document |
| `chat/services/context_service.py` | chat → graph.services.context_service |
| `chat/services/inference_service.py` | chat → graph.services.inference_service |
| `chat/services/media_service.py` | chat → media.services.upload |
| `chat/services/gpu_lock.py` | chat → graph.services.gpu_lock |
| `chat/services/minio_service.py` | chat → common.storage.minio_service |
| `chat/services/generation.py` | chat → common.exceptions (部分) |
| `graph/prompts.py` | graph → context |
| `context/tokenizer.py` | context → common.tokenizer |
| `graph/services/agent_helpers.py` | agent_helpers → helpers/ 子包 |

✓ 仍有活跃调用者经由兼容层导入（不能直接删除，需先迁移调用方）。
**安琳决定**：本轮重构统一清理。

### 死文件

| 文件 | 状态 |
|------|------|
| `ContextMonitorPanel.design.tsx`（680 行） | ✓ 无 import 引用，**安琳确认可删除** |
| `apps/models/tests.py`（3 行空桩） | ✓ **安琳确认可删除** |

---

## 二、性能痛点（主观感受，待 Phase 1 用日志/trace 验证）

### LLM 首 token 延迟
- 待验证：宪法要求 < 2s
- 待验证：PromptBuilder 记忆召回串行 + Langfuse trace 启动开销

### 语音链路 ⭐ 安琳重点关注

**安琳感受（端到端延迟）**：
- **现象**：从"我说完话"到"小爱音响播放出回复"的端到端延迟明显偏大
- **目标 SLO**：**整体控制在 5 秒以内**（当前实际未测，推测超标）
- **完整链路**：reSpeaker 拾音 → WiFi 桥接上报（016）→ VAD 切分 → ASR → Agent 推理（含 SubAgent/工具）→ TTS 合成 → HA/小爱下发播报
- **重点排查位置**（待 call-chain-profiler 定位）：
  - reSpeaker WiFi 桥接上行延迟（016 新增，网络因素）
  - ASR 是否在 VAD 结束前就开始流式识别？还是等完整切片
  - Agent 推理是否存在 SubAgent 串行链路？
  - TTS 是否等全部 token 完成再合成？还是流式 chunk 合成
  - HA 下发到小爱是否有额外排队

**注意**：这个 5 秒 SLO 是**端到端"可听延迟"**，与宪法中"LLM 首 token < 2s"是**不同维度的指标**，不能混淆。

**其他语音痛点**：
- 待验证：reSpeaker WiFi 桥接在弱网下的掉包后恢复延迟

### 文档处理
- 待验证：embedding 生成是否阻塞主线程
- 待验证：pgvector 混合搜索在大量文档下的退化

### ContextMonitor
- 待验证：监控面板推送是否拖慢主流程

### Celery 任务
- 待验证：5 个定时任务是否有堆积

---

## 二·B、已知功能缺陷 ⭐ 安琳反馈

> 与性能无关的功能性 bug，但会直接影响产品体验和重构决策，必须在 refactor-plan 中作为独立 P0 考虑。

### 声纹识别结果错误（017-ambient-speaker-id 功能缺陷）

- **现象**：前端消息列表中，`speaker_id` 显示**始终是 `dantsinghua`**，即使实际说话人不是安琳本人
- **影响面**：017 特性的核心功能失效，家庭多用户场景（015）完全无法区分成员
- **涉及模块**：
  - 后端 `apps/voice/services/speaker_service.py`（声纹匹配逻辑）
  - 后端 `apps/voice/consumers/*`（speaker_id 生成）
  - 后端 `apps/chat/models.py` Message.speaker_id 字段
  - 前端消息列表组件（speaker_id → 显示名映射）
- **可能根因（待 Phase 1 分析，列出 3 条候选假设）**：
  1. **H1（最可能）**：`SpeakerProfile` 注册样本库只有 `dantsinghua` 一条，任何语音都被匹配到唯一样本
  2. **H2**：`speaker_service` 匹配逻辑的兜底默认值返回了第一个/最新的 profile（即 dantsinghua）
  3. **H3**：前端 `speaker_id` → username 映射错误（后端返回正确但前端显示错）
- **安琳决定**：本 bug 属于 **P0 Day-1 必修项**，不是重构而是修复
  - Phase 1 的 refactor-planner 应**独立生成一个 fix batch**（不是 refactor batch）
  - 该 batch 优先于所有其他 P0 可观测性 batch（因为它已经在影响真实使用）

---

## 三、代码热点区（高频修改 / 复杂度高）

### 后端 Top 疑似上帝模块

| 修改次数 | 行数 | 文件 | 风险 |
|---------|------|------|------|
| 25 | 513 | `core/settings.py` | 高 — 全项目配置集中，117 个 getenv |
| 11 | 246 | `graph/services/agent_service.py` | 高 — Agent 核心编排 |
| 10 | 162+750 | `voice/consumers.py` + 3 Mixin | 中 — 3 Mixin 组合入口 |
| 10 | 190 | `voice/services/voice_pipeline.py` | 中 — 语音管道编排 |
| 9 | 229 | `voice/services/response_decision_service.py` | 中 — 017 新增 |

### 前端 Top 复杂组件

| 行数 | 修改次数 | 文件 | 说明 |
|------|---------|------|------|
| 527 | — | `hooks/useVoiceMode.ts` | 8 态 FSM |
| 396 | 10 | `hooks/useChatStream.ts` | 流式核心 |
| 423 | — | `hooks/useVoiceWebSocket.ts` | WS 管理 |
| 509 | 8 | `components/chat/MessageList.tsx` | 消息列表 |
| 521 | — | `components/chat/MessageInput.tsx` | 输入框 |

---

## 四、测试覆盖实情

### 实测覆盖率

- 总体：**79%**（目标 80%，差 1%）
- 1573 passed, **13 failed**, 9 skipped
- 关键服务层覆盖优秀：`response_decision_service` 99%, `voice_pipeline` 97%, `speaker_service` 98%, `tts_router` 95%

### 失败测试（13 个，⏳ 需排查确认根因）

| 测试文件 | 失败数 | 推测原因 |
|---------|--------|---------|
| `test_media_cleanup_task.py` | 8 | 可能因 017 分支 media 模型变更导致 mock 失配 |
| `test_models.py` | 1 | 模型关系变更 (cascade_delete) |
| `test_tasks.py` | 3 | 月度总结逻辑变更 |
| `test_document_agent.py` | 1 | SSE 进度流测试 |

### 已知覆盖不足的模块

| 文件 | 覆盖率 | 说明 |
|------|--------|------|
| `consumer_inference.py` | **54%** | InferenceMixin，**安琳决定补充 + 完善 API 契约** |
| `users/views.py` | **50%** | 5 个视图类，**安琳决定补充** |
| `users/serializers.py` | **64%** | 序列化验证 |
| `voice_persist_service.py` | **66%** | ambient 持久化，**安琳决定补充** |
| `reset_all_data.py` | **68%** | 全量重建命令，**安琳决定保留** |

### 端到端测试缺口
- 待验证：家庭多用户（015）切换场景（**与二·B 声纹 bug 强相关**）
- 待验证：reSpeaker WiFi 桥接（016）弱网场景
- 待验证：长对话（10+ 轮）上下文裁剪是否正确

---

## 五、"没人敢动"区（高风险修改路径）

### 数据一致性敏感区
- ✓ `Message.status` 转换（0→1→2→3）— 错误转换会丢消息
- ✓ SSE 事件格式 — 前端强依赖
- ✓ `apps/users/` SM3/SM4 加密 — 动了老用户无法登录

### 业务逻辑黑盒
- ✓ PromptBuilder 记忆召回策略（0.7 向量 + 0.3 关键词）
- ✓ LangGraph 主 Agent → SubAgent 路由决策
- ✓ Token 预算裁剪算法

### 外部依赖锁定
- ✓ Gateway 文档解析 API 格式
- ✓ Home Assistant Hass API（007）
- ✓ Langfuse trace_id 格式

### voice 模块（✓ 扫描确认最高风险）
- 2,794 LOC / 14 文件 / 7 轮迭代
- 3-Mixin 共享 `self._*` 无接口约束 → **安琳决定方案 B 组合模式重构**
- `except Exception` 集中在 graph/ 模块（143 处），**安琳决定分批缩减**

### chat↔graph 循环依赖（架构分析确认）
- 16 个跨边文件，graph 重度依赖 chat 的 Message/Execution 模型
- **安琳决定方案 B** — 把 Message/LangGraphExecution 模型抽到 `core/models.py`，chat 和 graph 都依赖 core

---

## 六、已识别但未解决的 TODO（重构目标池）

### P0 Day-1 修复（优先于所有重构）
- [x] **声纹识别显示错误修复** — 安琳强需求，详见第二·B 节

### 可观测性（P0）
- [ ] trace_id 没贯穿全链路（主 Agent / SubAgent / LLM Gateway / Celery / 前端 SSE），跨服务排查困难
- [ ] 日志格式不统一（部分结构化部分纯文本）
- [ ] 性能指标缺少持续采集（只有 Langfuse，没有应用级 metrics）
- [x] **端到端语音延迟埋点**（覆盖 reSpeaker→ASR→Agent→TTS→HA 全链路，为 5s SLO 提供基线数据）— **安琳已批准**

### 性能（P1）
- [ ] 首 token 延迟超标的具体原因未定位
- [x] **端到端语音链路延迟优化（目标 < 5s）** — **安琳已批准**，核心痛点
- [ ] PromptBuilder 记忆召回是否可并行（`asyncio.gather`）
- [ ] pgvector 索引是否最优（IVFFlat vs HNSW）
- [ ] Redis 连接池配置（4 个 DB 是否复用连接）

### 技术债（P2）
- [x] `core/settings.py` (513 行) 拆分 — **安琳已批准**
- [ ] 23 个 Jinja2 模板的渲染缓存
- [ ] 6 个 SubAgent 的工具注册统一化
- [ ] 前端 Zustand 5 个 store 的边界是否清晰
- [x] 12 个兼容 shim 统一清理 — **安琳已批准**
- [x] `chat/services/types.py` + `generation.py` 迁移到 graph — **安琳已批准**
- [x] voice 3-Mixin 架构整理 → **方案 B 组合模式**（Mixin 改为独立 Service 类） — **安琳已批准**
- [x] `except Exception` 143 处分批缩减 — **安琳已批准**

### 测试（P3）
- [x] `consumer_inference.py` 54% → 补充 + 完善 API 契约 — **安琳已批准**
- [x] `users/views.py` 50% → 补充 — **安琳已批准**
- [x] `voice_persist_service.py` 66% → 补充 — **安琳已批准**
- [ ] E2E 关键路径自动化
- [ ] 性能回归测试基线

### 其他
- [x] 13 个失败测试 → **需排查根因后修复**

---

## 七、重构禁区（本轮明确不做）

Phase 1 分析时**不要**把以下事项放进 refactor-plan：

1. **PostgreSQL schema 变更** — migration 成本高，生产数据量大
2. **SSE 事件格式变更** — 前端强耦合，改一个字段全线联调
3. **SM3/SM4 加密方案** — 合规锁定，动了用户无法登录
4. **"单用户单会话"模型** — 宪法明文禁止引入 conversation_id
5. **LangGraph / LangChain 版本升级** — 本轮只重构业务代码，不动底层框架
6. **前端技术栈迁移** — Next.js 14 / React 18 / Zustand 保持不变
7. **Docker 服务拓扑调整** — 9 个服务保持现状
8. **Gateway API 契约变更** — 跨服务合约

---

## 八、重构优先级（Phase 1 分析必须按此排序）

```
P0 Day-1 fix  →  P0 可观测性  →  P1 性能     →  P2 技术债      →  P3 测试补齐
(先不崩)         (先能看见)      (再能优化)     (再能重构)         (最后固化)
```

**关键原则**：先让"已有的 bug"不崩，再让"看不见的系统"变"看得见"，再动刀。

### P0 Day-1（本轮优先级最高）
- **声纹识别 bug 修复**（第二·B 节）
- **13 个失败测试修复（恢复 CI 绿色）**

### P0：可观测性（第一批 batch）
- trace_id 贯穿
- 结构化日志统一
- 关键指标埋点
- **端到端语音延迟埋点**（为 5s SLO 提供基线，优先级高于通用 metrics）

### P1：性能（2-4 个 batch）
- **端到端语音延迟优化（目标 < 5s）** ⭐ 安琳核心痛点
- 首 token 延迟分解
- 记忆召回并行化
- 缓存机会挖掘

### P2：技术债（按热点排序）
- settings.py 域拆分
- 12 个兼容 shim 清理 + types/generation 迁移
- voice Mixin 架构整理
- except Exception 分批缩减
- DocumentParseService 兼容委托清理
- 死文件删除（design.tsx, models/tests.py, DEPRECATED diarize）

### P3：测试（贯穿始终）
- 每个 P0-P2 batch 必须带测试
- 低覆盖模块补全（consumer_inference, users/views, voice_persist）
- 独立批次补齐 E2E

---

## 九、给 Claude 的分析提示

运行 Phase 1 分析时，注意以下事项：

1. **证据优先**：所有结论必须能指到具体文件:行号或具体日志
2. **区分客观与主观**：本文档的"待验证"部分要用日志/trace 验证
3. **不要替我决策**：遇到本文档没写的事项，在产出里标"Pending Decision"
4. **遵守禁区**：第七节的 8 条禁区不可越界
5. **尊重优先级**：第八节的 P0-P3 顺序不可颠倒
6. **第二·B 声纹 bug 是 fix 而非 refactor**：refactor-planner 必须单独给出 fix batch，且位列所有重构 batch 之前
7. **端到端语音延迟是主指标**：5s SLO 是安琳的核心痛点，call-chain-profiler 必须专门分析这条链路

---

*本文件由安琳维护，随重构进展更新。最后更新：2026-04-16*
*数据来源：legacy-scanner 自动扫描（docs/legacy-scan-draft.md）+ 安琳 Q&A 确认 + 安琳主观感受补充*
