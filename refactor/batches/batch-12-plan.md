# Batch-12 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P1 (performance) | 风险：medium
> 预估：4 文件 / ~80 行 / 1 session
> 依赖：depends_on=[]（无前置依赖，✅ 满足）
> SLO 影响：blocks_slo=null；目标削减 build_prompt_preamble 阶段 ~50-100ms/pipeline
> 基线：main HEAD=84865f2（含 batch-04~11+28；batch-08 AmbientLightPipeline / batch-11 共享 get_redis 连接池已在树上）

## 1. 任务理解（一句话）

把 `build_prompt_preamble` 里三步彼此无数据依赖的串行 IO（取激活模型 / 记忆召回 / 取历史消息）改为 `asyncio.gather` 并行；给 `ModelService.get_active_model` 加 60s 进程内 TTL 缓存（惠及全部 8 处调用方，含 batch-08 AmbientLightPipeline）；给 `_load_wake_words` 的 DB 查询加缓存。三项净目标 ~50-100ms/pipeline。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/models/services.py | 86 | +25 -3 | 加 TTL 缓存 + 失效钩子 | 中 | 低 |
| 2 | backend/apps/graph/services/helpers/prompt.py | 86 | +25 -12 | 三步 IO 并行化 | 中 | 低 |
| 3 | backend/apps/voice/services/response_decision_service.py | 240 | +18 -3 | wake_words 缓存 | 低 | 中（240 行，未破 300 硬限） |
| 4 | backend/tests/apps/graph/test_prompt_builder.py | 0（新建） | +90 | 新增测试 | 低 | — |

均未超 300 行硬限制。无需拆分。

## 3. 详细改动计划

### 文件 1: backend/apps/models/services.py — model_config 60s TTL 缓存

#### 改动 1.1（模块级缓存容器，line 10 之后）
- 新增：
  ```python
  import time
  _MODEL_CACHE_TTL = 60.0  # 秒；per-process，缓存解密后 dict
  _model_cache: dict[str, tuple[float, dict[str, Any]]] = {}

  def _invalidate_model_cache(model_type: str | None = None) -> None:
      if model_type is None:
          _model_cache.clear()
      else:
          _model_cache.pop(model_type, None)
  ```
- 理由：进程内缓存，缓存的是 `decrypt=True` 的明文 dict。仅存内存、**绝不落日志**（现有日志只打字段名/id，无 key，保持）。

#### 改动 1.2（get_active_model，line 77-83）
- 当前：每次调用都 `model_repo.get_active_by_type` + SM4 解密。
- 改为：命中缓存（未过期）直接返回缓存 dict 的**拷贝**；未命中查库解密后写缓存。
  ```python
  @staticmethod
  def get_active_model(model_type: str) -> Optional[dict[str, Any]]:
      cached = _model_cache.get(model_type)
      if cached and (time.monotonic() - cached[0]) < _MODEL_CACHE_TTL:
          return dict(cached[1])          # 返回副本，防调用方污染缓存
      model = model_repo.get_active_by_type(model_type)
      if not model:
          logger.warning(f"No active model found for type: {model_type}")
          return None
      result = _to_dict_with_key(model, decrypt=True)
      _model_cache[model_type] = (time.monotonic(), result)
      return dict(result)
  ```
- 理由：8 处调用方（agent.py:69 / multimodal_agent.py:29 / prompt.py:17 / chat/types.py:27 / memory/task_helpers.py:20 / memory/services.py:37 / ambient_light_service.py:48 / response_decision_service.py:75）全部受益。返回副本避免"某调用方 mutate 返回 dict → 污染后续缓存"。
- 预估行数：+8 -2

#### 改动 1.3（update_model 末尾失效钩子，line 74 之后）
- 在 `logger.info(...)` 后加 `_invalidate_model_cache(model.type)`。
- 理由：经 `update_model` 的在线修改立即失效，满足"即时生效"（a2c1ac3 引入的设计意图）。
- ⚠️ 注意：MEMORY.md 记录的历史模型切换是**直接 ORM 改 ModelConfig 表**（绕过 update_model），此路径不会触发失效 → 依赖 60s TTL 兜底。见第 7 节决策项。
- 预估行数：+1

### 文件 2: backend/apps/graph/services/helpers/prompt.py — 三步 IO 并行化

#### 数据依赖分析（Open Question Q1，已确认）
三步 IO 的**输入互不依赖**，仅各自产出后续消费值：
- A `get_active_model("tool")`（line 17）→ 产 `max_context_window`/`model_name`（line 18-20, 64 消费）
- B `search_memory(user_id, user_message, ...)`（line 28）→ 产 `memory_results`/`retrieved_memories`（line 45 消费）
- C `find_latest_by_user(user_id, limit=...)`（line 50）→ 产 `history_messages`（line 55 消费）

A/B/C 无一消费彼此输出 → **可 gather 并行**。C 当前位于 build_preamble 之后（line 50），但其入参 `user_id`+`settings.CONTEXT_HISTORY_ROUNDS` 恒定，可安全上提。

#### 改动 2.1（line 17-31, 50-52 → 合并为一处 gather）
- 用 `asyncio.gather(..., return_exceptions=True)` 并发三 coroutine，随后逐个还原**现有异常语义**：
  ```python
  import asyncio
  async def _memory_task():
      if not user_message:
          return []
      from apps.memory.services import MemoryService
      return await MemoryService.search_memory(
          user_id=user_id, query=user_message,
          limit=settings.MEMORY_SEARCH_TOP_K, skip_vector=False)

  mc_r, mem_r, hist_r = await asyncio.gather(
      sync_to_async(model_service.get_active_model)("tool"),
      _memory_task(),
      message_repo.find_latest_by_user(
          user_id, limit=getattr(settings, "CONTEXT_HISTORY_ROUNDS", 10) * 2),
      return_exceptions=True,
  )
  # A: model_config —— 原本无 try/except，异常应传播
  if isinstance(mc_r, BaseException):
      raise mc_r
  model_config = mc_r
  # B: memory —— 原本 try/except 降级为空（line 42-43 语义）
  memory_results, retrieved_memories = [], None
  if isinstance(mem_r, BaseException):
      logger.warning("memory recall failed", extra={"user_id": user_id, "error": repr(mem_r)})
  elif mem_r:
      memory_results = mem_r
      retrieved_memories = [RetrievedMemory(...) for r in mem_r]  # 同现有 line 34-41
  # C: history —— 原本无 try/except，异常应传播
  if isinstance(hist_r, BaseException):
      raise hist_r
  history_messages = hist_r
  history_messages.reverse()
  ```
- 保持 `PromptConfig`/`builder` 构造（line 20-21）在 gather 之后（依赖 A 的 max_context_window）。line 45 之后逻辑不变。
- 理由：并行掉 A+B+C 三段网络/DB IO（原本串行累加），预计省 50-100ms。`return_exceptions=True` 是保留"memory 失败降级、model/history 失败传播"三种既有语义的关键。
- ⚠️ LLM 异常分类红线：本函数不直接调 LLM；search_memory 内部的 embedding 异常仍被 B 分支的 warning 降级捕获，分类语义不变。
- 预估行数：+22 -12

### 文件 3: response_decision_service.py — wake_words 缓存

#### 改动 3.1（模块级缓存，line 24 附近）
```python
_WAKE_WORDS_TTL = 60.0
_wake_words_cache: dict[int, tuple[float, list[str]]] = {}

def invalidate_wake_words_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _wake_words_cache.clear()
    else:
        _wake_words_cache.pop(user_id, None)
```

#### 改动 3.2（_load_wake_words，line 163-169）
- 命中未过期缓存直接返回；未命中查 `voice_settings_repo.get_or_create` 后写缓存。异常分支仍回落默认词（不缓存失败结果）。
- 理由：`decide()` 每次都查 DB（line 46）；wake_words 极少变更。
- 预估行数：+12 -1

#### 失效策略（二选一，见第 7 节决策项）
- 方案 A（默认，零额外文件）：仅 60s TTL，改词后最多 60s 生效。
- 方案 B：在 `voice_settings_service.update_settings`（voice_settings_service.py:50）调用后加 `invalidate_wake_words_cache(user_id)` → 即时生效，但**超出声明 scope（+1 文件）**。

### 文件 4: backend/tests/apps/graph/test_prompt_builder.py（新建）
详见第 5.5 节测试清单。

## 4. 调查步骤（本 batch 为 refactor，非 fix）

不适用。数据依赖分析已在 3.2 完成，Q1 结论：三步 IO 输入互不依赖，可并行。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/apps/graph/test_prompt_builder.py -v`（新建）
- [ ] `pytest backend/tests/voice/test_response_decision.py backend/tests/voice/test_response_decision_llm.py -v`（wake_words + model 缓存不回归）
- [ ] `pytest backend/tests/models/test_services.py -v`（get_active_model 缓存不破坏既有断言）
- [ ] `pytest backend/tests/chat/test_services.py backend/tests/chat/test_agent.py -v`（build_prompt_preamble 调用方）
- [ ] `pytest backend/tests/voice/test_ambient_light_service.py -v`（batch-08 共享 model 缓存）
- [ ] `ruff check backend/apps/models backend/apps/graph/services/helpers backend/apps/voice/services`
- [ ] `mypy backend/apps/models/services.py backend/apps/graph/services/helpers/prompt.py`

### 5.2 手动验证
- [ ] 对比优化前后 build_prompt_preamble 阶段耗时（trace/日志 stage 时长）

### 5.3 性能验证（P1）
- [ ] 若有 `refactor/baselines/batch-12-before.json`：跑对比脚本；预期 build_prompt_preamble 阶段 -50~100ms
- [ ] 首次调用（冷缓存）不应比现状慢；第二次调用（热缓存）model_config 查库应消失

### 5.4 回归验证
- [ ] `pytest backend/tests/models backend/tests/graph backend/tests/voice backend/tests/chat -q`
- [ ] 全量：`pytest backend/ -q`（确认 1278+ 基线不掉）

### 5.5 新增测试清单
test_prompt_builder.py（新建）：
- [ ] `test_preamble_runs_three_io_in_parallel`：patch 三个 IO 各 sleep 50ms，断言总耗时 < 120ms（证明并行）
- [ ] `test_preamble_memory_failure_degrades`：search_memory 抛异常 → 不抛出、retrieved_memories 为空、其余正常
- [ ] `test_preamble_model_config_failure_propagates`：get_active_model 抛异常 → 传播（保留原语义）
- [ ] `test_preamble_history_failure_propagates`：find_latest 抛异常 → 传播
- [ ] `test_preamble_returns_seven_values`：返回 7 元组结构不变

test_services.py（models，追加）：
- [ ] `test_get_active_model_cache_hit_skips_db`：连续两次调用，第二次不查 repo（mock 计数）
- [ ] `test_get_active_model_cache_ttl_expiry`：monkeypatch time 越过 60s → 重新查库
- [ ] `test_update_model_invalidates_cache`：update_model 后缓存失效
- [ ] `test_cache_returns_copy_not_shared`：mutate 返回 dict 不影响下次

test_response_decision.py（追加）：
- [ ] `test_wake_words_cache_hit`：连续 decide 只查一次 DB
- [ ] ⚠️ **autouse fixture 清 `_wake_words_cache` 与 `_model_cache`**：防跨用例污染（现有用例用不同 wake_words 参数化，缓存持久会串味）

## 6. 回滚策略

`git revert <commit>`（单 commit）。三处改动均为纯内存/并发优化，无 schema/migration/API 契约变更，revert 后行为完全回到 HEAD。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **Q1 确认**：三步 IO 无因果依赖的结论（第 3.2 分析）是否认可可直接 gather？
- [ ] **model_config 缓存失效窗口**：经 update_model 的在线改配置即时失效（钩子已加）；但**直接 ORM 改表**（如 MEMORY.md 记录的历史模型切换）只能靠 60s TTL 兜底 —— 60s 内生效是否可接受？还是需暴露一个手动 flush 入口/管理命令？
- [ ] **wake_words 失效策略**：选方案 A（仅 60s TTL，零额外文件）还是方案 B（在 voice_settings_service.py:50 加失效钩子即时生效，但**触碰 scope 外 +1 文件**）？
- [ ] **测试文件 scope 微扩**：为避免进程内缓存跨用例污染，需在 `test_response_decision.py` 与 `test_services.py`（models）追加 autouse 清缓存 fixture —— 这两个测试文件不在声明的 files_touched（scope 只列了 test_prompt_builder.py）。是否批准追加？（纯测试改动，零业务风险）
- [ ] **缓存 dict 明文 key**：model 缓存持有 SM4 解密后明文 api_key（内存 60s，不落日志）。确认此内存留存可接受（现状每次调用也在内存中短暂持有明文）。

## 8. 执行预算

- 预计 tool calls：~25（3 处编辑 + 1 新测试 + 多轮 pytest/ruff/mypy）
- 预计 token：中等（单 session 内）
- 预计完成：1 session，与 estimated_sessions=1 吻合，未超 2 倍。无需拆分。
