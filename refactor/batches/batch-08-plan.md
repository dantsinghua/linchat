# Batch batch-08 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P1 (performance) | 风险：high
> 预估：5 文件 / 350 行 / 2 session
> 依赖：batch-07 → ✅ COMPLETED（batch-04/05/06/07/28 全部已并入 main）
> SLO 影响：blocks_slo = voice_end_to_end_5s（本 batch 是 5s 攻坚最大收益点，预计 -4~5s）

## 1. 任务理解（一句话）

为 ambient 语音模式新建一条轻量推理路径 `AmbientLightPipeline`：只用「精简 system prompt + 最近 3 轮历史（6 条 user/assistant）+ 用户消息」，直接用 httpx 流式调 LLM Gateway 的 `/v1/chat/completions`，跳过 LangGraph/SubAgent/工具/完整记忆召回，把 ambient 的 LLM 推理 P50 从 6.3s 降到 ~1.5-2s；voice_chat 模式完全不动，继续走 `AgentService.execute` 完整 Agent。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/graph/services/ambient_light_service.py（**新建，见§7 待决策 D1**） | 0 | +120 | 新建轻量 pipeline | 高 | 低（新文件保持精简） |
| 2 | backend/apps/voice/services/voice_pipeline.py | 240 | +18 -6 | ambient 分支切换 | 中 | 低 |
| 3 | backend/core/settings.py | 477 | +3 | 新增开关 | 低 | 中（477>300，见§7 D2，配置文件不建议拆） |
| 4 | backend/apps/context/templates/ambient_light_prompt.j2（新建） | 0 | +25 | 新建 prompt 模板 | 低 | 低 |
| 5 | backend/apps/graph/services/agent_service.py | 256 | 0（不改，见§7 D1） | — | — | 中 |
| 6 | backend/tests/graph/test_ambient_light_service.py（新建测试） | 0 | +90 | 新增测试 | 低 | — |
| 7 | backend/tests/voice/test_voice_pipeline.py | ~230 | +40 | 新增 ambient 分支测试 | 低 | — |

> 说明：04-plan.json 的 files_touched 里写了 agent_service.py，但把 ~120 行轻量路径塞进 256 行的 agent_service.py 会突破 300 行硬限制。故本计划建议**新建 ambient_light_service.py**（见§7 D1）。此为 scope 微调，须安琳确认。

## 3. 详细改动计划

### 文件 1: backend/apps/graph/services/ambient_light_service.py（新建）

参考现有直调 Gateway 的成熟范式 `apps/graph/multimodal.py:50 stream_multimodal_httpx`（**禁止另起炉灶**）。

#### 改动 1.1 — 类骨架与配置读取
- 新增 `class AmbientLightPipeline`，静态方法 `stream(user_id, request_id, user_text) -> AsyncGenerator[StreamChunk, None]`。
- 读取模型配置：`model_service.get_active_model("tool")`（`apps/models/services.py:77`）。该方法内部已用 `sm4_decrypt` 返回**明文 api_key**（`services.py:39,83`）——**禁止重新实现 SM4 解密**，直接取 `config["api_key"]`。
- URL 拼装照抄 multimodal.py:56-58：`base_url = config["url"].rstrip("/")`，若不以 `/v1` 结尾则补 `/v1`。
- 理由：复用唯一可信的配置与解密链路，红线「API 密钥必须 SM4 加密 / 禁止重新实现解密」。

#### 改动 1.2 — 组装 messages（system + 最近 3 轮 + user）
- system：`from apps.context.loader import render` → `render("ambient_light_prompt.j2", today_date=..., user_timezone=...)`（loader 已有 Jinja2 env，见 `apps/context/loader.py:15`）。
- 历史：`message_repo.find_latest_by_user(user_id, limit=settings.VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS * 2)`（`apps/chat/repositories.py:69`）。
  - **术语红线**：「保留最近 3 轮」= 3×2 = **6 条** user/assistant 消息；`limit=rounds*2`。
  - 返回按 `-created_time` 倒序 → 需 `.reverse()`；过滤空 content，assistant 去掉 `[已中断]` 后缀（照 `prompt.py:57-62` 逻辑）。
  - 隔离粒度红线：查询只按 `user_id`，无 conversation_id/session_id。
- messages = `[{"role":"system",...}] + history_dicts + [{"role":"user","content":user_text}]`。

#### 改动 1.3 — httpx 流式调用（照抄 multimodal.py:68-101）
- `httpx.AsyncClient(timeout=httpx.Timeout(settings.LLM_CALL_TIMEOUT, connect=10.0))`。
- `client.stream("POST", f"{base_url}/chat/completions", headers={Authorization: Bearer <key>}, json={model, messages, stream:True, max_tokens: config.get("max_output_tokens",1024)})`。
- 逐行解析 `data: ` SSE，`[DONE]` 结束；`chunk["choices"][0]["delta"]["content"]` → `yield StreamChunk(type="content", content=delta, message_id=...)`。
- 首条 content chunk 带上 `request_id`（对齐 agent_service.py:131 的行为，供前端识别）。
- 不引入新依赖（httpx 已在用）。

#### 改动 1.4 — 消息持久化（关键，勿丢）
- ambient 轻量路径**绕过了 AgentService**，因此 AgentService 里的建消息逻辑不再执行，必须自己落库，否则本轮对话不进历史、下一轮拿不到上下文。
- 复用 `create_first_token_messages(...)`（`helpers/finalize.py:92`）在首 token 时创建 user+assistant 两条 Message（同 request_id、sequence=max_seq+1/+2）。这样 voice_pipeline.py:174-182 既有的「ambient 用 ASR 原文覆盖 user content」逻辑仍生效。
- 结束时更新 assistant content/status=NORMAL、`user_repo.add_message_count(user_id,2)`、`add_tokens`（可复用 finalize 中同款调用；tokens 从 SSE `usage` 字段取，取不到则记 0）。
- 需要 `max_seq = await message_repo.get_max_sequence(user_id)`（`repositories.py:59`）。

#### 改动 1.5 — LLM 异常分类（红线）
- 整个 httpx 段 `try/except Exception as e:` → `mapped = map_llm_exception(e)`（`apps/common/exceptions.py:109`，**已内置** httpx.TimeoutException→LLMTimeoutError、ConnectError→LLMConnectionError、429→LLMRateLimitError、content filter→LLMContentFilterError 的分类，直接复用，勿手写）。
- Gateway 返回非 200 或 body 含 `error` 时，抛 RuntimeError 交给 map_llm_exception（照 multimodal.py:74,90）。
- 捕获后 `yield StreamChunk(type="error", content=mapped.message)`，使 voice_pipeline 既有的 `chunk.type=="error"` 分支（voice_pipeline.py:116-122，触发 TTS 错误播报）无缝复用。

### 文件 2: backend/apps/voice/services/voice_pipeline.py

#### 改动 2.1 — ambient 分支切换生成器（`_run_inner`，第 99-102 行附近）
- 现状：无论 mode 都 `async for chunk in AgentService.execute(...)`。
- 改动：在进入 async-for 前按 mode + 开关选择生成器：
  ```python
  if mode == "ambient" and settings.VOICE_AMBIENT_LIGHT_ENABLED:
      from apps.graph.services.ambient_light_service import AmbientLightPipeline
      agent_gen = AmbientLightPipeline.stream(user_id, request_id, text)  # 传 ASR 原文
  else:
      agent_gen = AgentService.execute(
          user_id=user_id, thread_id=f"user_{user_id}",
          request_id=request_id, user_message=voice_text)
  async for chunk in agent_gen:
      ...  # 循环体完全不变
  ```
- 关键点：
  - 轻量路径传 `text`（ASR 原文），把「纯口语/禁 Markdown/简洁」的指令收进 j2 system prompt，而非拼在 user message 前缀（voice_text）。
  - 循环体、`latency_record("llm_first_token"/"llm_total")`（第 110/129 行）、TTS enqueue、error 分支**一律不改** → batch-07 埋点与 barge-in/取消行为零回归。
- 理由：最小侵入，voice_chat 完全走原路径。

### 文件 3: backend/core/settings.py

#### 改动 3.1 — 新增开关与参数（VOICE_ 段，第 440 行附近）
```python
VOICE_AMBIENT_LIGHT_ENABLED = os.getenv("VOICE_AMBIENT_LIGHT_ENABLED", "true").lower() == "true"  # ambient 轻量推理开关（关=回退完整 Agent）
VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS = int(os.getenv("VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS", "3"))  # 保留最近 N 轮（N×2 条）
```
- 理由：settings 开关是**首选回滚手段**——无需 revert 代码，改环境变量即可让 ambient 秒回完整 Agent。

### 文件 4: backend/apps/context/templates/ambient_light_prompt.j2（新建）

- 内容要点（≤25 行）：精简人设 + 「纯口语、对话式、禁止任何 Markdown（**加粗**/#标题/-列表/编号）、简洁不长、TTS 播报」硬约束 + `{{ today_date }}` / `{{ user_timezone }}` 占位。
- 不引入 memory_context/tool_usage 等重模块（这正是省 token 的核心）。
- 理由：替代 PromptBuilder 构建的长 system prompt，是「输入 token 降 80%+」的来源。

## 4. 调查步骤（fix 类专用）

不适用（本 batch 为 refactor/performance，非 fix）。瓶颈定位已由 03-call-chain-analysis 确认：
- 综合瓶颈 #1 = 「LLM 推理走完整 Agent（ambient）」P50=6.3s，占端到端 59%（voice_pipeline.py:87 + agent_service.py:33）。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/voice/test_voice_pipeline.py -v`（04-plan.json 指定）
- [ ] `pytest backend/tests/graph/test_ambient_light_service.py -v`（新增）
- [ ] `ruff check backend/apps/graph/services/ambient_light_service.py backend/apps/voice/services/voice_pipeline.py`
- [ ] `mypy backend/apps/graph/services/ambient_light_service.py`

### 5.2 手动验证步骤（须安琳执行，见§7）
- [ ] 触发 5 次 ambient 语音，读 latency.summary 汇总行，对比 `llm_total` 优化前(≈6.3s)后(目标<2.5s)
- [ ] 确认 ambient 短回复质量不明显退化（口语化、无 Markdown）
- [ ] 触发一次 voice_chat 模式，确认仍走完整 Agent（工具/记忆可用），无回归

### 5.3 性能验证（P1 batch）
- [ ] 优化前基线：`refactor/baselines/batch-08-before.json`（若无则先在 main 采一次 5 段 ambient 的 latency.summary）
- [ ] 优化后：`refactor/baselines/batch-08-after.json`
- [ ] 指标门槛（04-plan.json）：LLM 推理阶段 P50 < 2.5s（从 6.3s）；端到端 P50 < 7s

### 5.4 回归验证
- [ ] `pytest backend/tests/voice/ -v`
- [ ] `pytest backend/tests/graph/ backend/tests/chat/ -v`（本 batch 触及 graph 服务层）
- [ ] 确认 voice_chat 路径测试全绿（AgentService.execute 未改）

## 6. 回滚策略

04-plan.json：`git revert <commit>；恢复到完整 Agent 路径`。

分层回滚（从轻到重）：
1. **最快**：设 `VOICE_AMBIENT_LIGHT_ENABLED=false` 重启后端 → ambient 立即回退完整 Agent，无需改代码。
2. 代码回滚：`git revert <commit-hash>`（单 commit）。
3. worktree 整批撤销：
   ```bash
   git worktree remove ../linchat-batch-08
   git branch -D refactor/batch-08
   ```

## 7. ⚠️ 需要安琳确认的事项

- [ ] **D1（scope 微调）**：04-plan.json 把新路径归到 agent_service.py，但塞进去会使其 256→~380 行、突破 300 行硬限制。建议**新建 `backend/apps/graph/services/ambient_light_service.py`**（agent_service.py 不改）。是否同意此 scope 微调（新增 1 个 .py，未在 new_files 声明）？
- [ ] **D2（300 行硬限制豁免）**：`core/settings.py` 当前 477 行 > 300。本 batch 仅追加 3 行开关，**不建议**在本 batch 拆分 settings（属独立重构、风险外溢）。请确认豁免。
- [ ] **D3（Open Question Q3 · 产品决策）**：ambient 轻量 prompt **是否需要记忆召回能力**？本计划默认**不做**记忆召回（这是省 4-5s 的关键），仅保留最近 3 轮历史。若产品要求 ambient「记得用户偏好」，需改为「异步/缓存记忆」方案，会削弱收益——请安琳拍板。历史轮数默认 3 轮（可 env 调），是否合适？
- [ ] **D4（测试文件超 scope）**：将新增 `tests/graph/test_ambient_light_service.py` 并扩充 `tests/voice/test_voice_pipeline.py`，二者不在 files_touched。测试新增按惯例默认允许，知会确认。
- [ ] **D5（手动性能验证）**：§5.2/5.3 需真实语音链路 + Gateway，无法机器自动化，须安琳手动触发并采集 latency.summary。

若以上 D1-D5 达成一致，即可进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：40-60（新建 2 文件 + 改 3 文件 + 跑测试迭代）
- 预计 token 消耗：中等（文件都 <500 行，无需全仓扫描）
- 预计完成时间：1-2 session，与 04-plan.json `estimated_sessions=2` 一致，未超 2 倍，无需拆分。
