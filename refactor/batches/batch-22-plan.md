# Batch-22 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估（04-plan）：10 文件 / 200 行 / 1 session
> 依赖：无（depends_on 为空，满足）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）
> main HEAD：52878e5（batch-21 已合入，为最新）

## 1. 任务理解（一句话）

审计 voice + graph 两模块 10 个高频文件里的 `except Exception`，把**异常面 100% 已知**的处收窄为具体异常类型，给**静默 pass** 的边界处补 `logger`，其余**顶层兜底/cleanup/工具契约**的宽捕获保留并注明理由——只做「收窄类型 + 补日志」，不改变任何一处「吞 vs 抛」的语义。

## 2. 涉及文件清单与改动预测

实测 10 个文件共 **49 处** `except Exception`（4 月清单为 79，batch-04~21 改动后现状不同）。

| # | 文件 | 行数 | 现 except | 收窄 | 补日志 | 保留 | 精简潜力 |
|---|------|-----|----------|------|-------|------|---------|
| 1 | voice/services/response_decision_service.py | 257 | 6 | 2 | 1 | 3 | 中 |
| 2 | voice/services/voice_pipeline.py | **309** | 5 | 0 | 2 | 3 | 低（>300，见§7） |
| 3 | voice/services/voice_persist_service.py | 143 | 5 | 0 | 0 | 5 | 低（全为补偿/兜底）|
| 4 | voice/consumers.py | 168 | 5 | 0 | 3 | 2 | 中（3 处 disconnect 静默 pass）|
| 5 | voice/consumer_session.py | **303** | 4 | 0 | 0 | 4 | 低（>300，见§7）|
| 6 | graph/services/context_service.py | 149 | 6 | 1 | 0 | 5 | 中 |
| 7 | graph/services/agent_service.py | 256 | 6 | 0 | 0 | 6 | 低（全为顶层/rescue）|
| 8 | graph/services/inference_service.py | 101 | 5 | 4 | 0 | 1 | 高（纯 Redis）|
| 9 | graph/services/helpers/monitor.py | 96 | 4 | 0 | 1 | 3 | 中 |
| 10 | graph/tools/memory.py | 95 | 3 | 1 | 0 | 2 | 中 |
| | **合计** | | **49** | **8** | **7** | **34** | |

收窄后 grep `except Exception` 计数：49 → **41**（收窄的 8 处替换为具体类型；补日志的 7 处仍保留 `except Exception` 字面但增加可观测性）。

## 3. 详细改动计划

标注：①=收窄  ②=保留(注明理由)  ③=补日志(保留宽捕获)

### 文件 1: voice/services/response_decision_service.py

- **L124 ① 收窄** `_llm_classify` 尾部兜底（L118 已单独捕获 `httpx.TimeoutException` 降级为 RECORD_ONLY）。剩余异常面=HTTP 状态错/连接错、JSON 解析、`choices[0]` 索引、`float()` 转换。
  改：`except Exception as e:` → `except (httpx.HTTPError, json_module.JSONDecodeError, KeyError, IndexError, ValueError, TypeError) as e:`。行为不变（仍 `return None` 落到规则链），保留 `logger.warning(..., exc_info=True)`。
  **中置信**：异常面推断可能不完全（如罕见 UnicodeDecodeError）→ 见 §7 待确认。
- **L145 ② 保留** `_fetch_intent_context` 取最近消息：best-effort 上下文增强，任一失败返回空列表不影响分类；已有 `logger.debug`。收窄会把未列异常从吞变抛，违反行为等价。保留。
- **L150 ② 保留** 同上（取记忆）。已有 debug 日志。
- **L185 ③ 补日志** `_load_wake_words` 兜底 `return settings.VOICE_DEFAULT_WAKE_WORDS`：当前**静默无日志**。保留宽捕获（DB 异常面不确定），新增 `logger.debug("load wake_words failed, using default: user=%s", user_id, exc_info=True)`。
- **L195 ① 收窄** `_get_recent_speaker_count` 兜底 `return 0`：纯 Redis 调用（`get_redis().scard`）。改 `except (RedisError, ConnectionError):`（`from redis.exceptions import RedisError`），并补 `logger.debug`。行为不变。
- **L229 ② 保留** `_is_tts_echo` 兜底 `return False`：Redis 调用但含 `SequenceMatcher`/list 迭代，异常面稍杂；已有 `logger.debug(exc_info=True)`。保留宽捕获（回声检测失败放行是安全侧）。

### 文件 2: voice/services/voice_pipeline.py

- **L201 ② 保留** pipeline 主流式循环**顶层兜底**：实时链路顶层，须捕获一切（LLM/网络/TTS），置 error_occurred + 发 PIPELINE_ERROR + TTS 错误音。已 `logger.error(exc_info=True)`。**边界兜底，保留**。
- **L218 ② 保留** finally 内 TTS cleanup（wait_idle/shutdown）：finally 清理路径，已 `logger.exception`。保留。
- **L225 ③ 补日志** finally 内 `TTSRouter.send_control("tts.completed")` 的 `except Exception: pass`：cleanup 路径静默。保留宽捕获，`pass` → `logger.debug("tts.completed control failed (ignored): user=%s", user_id, exc_info=True)`。
- **L250 ② 保留** ambient 更新用户消息 content：best-effort DB update，已有 `logger.debug`。保留。
- **L308 ③ 补日志** `_try_ha_speaker_tts` 外层（内层已捕 `HASpeakerError`）：best-effort 外设路由，已降级到浏览器。当前 `logger.error(..., e)` 无堆栈 → 加 `exc_info=True`。保留宽捕获。

### 文件 3: voice/services/voice_persist_service.py（5 处全保留）

- **L45 ② 保留** `delete_from_minio`：MinIO 补偿删除，补偿动作不可再抛；已 warning。
- **L68 ② 保留** `_atomic_mark_voice` 失败 → 补偿删 MinIO 后 **`raise`**：此处捕获后重新抛出（事务补偿模式），收窄会让部分异常跳过补偿。保留是正确模式。
- **L73 ② 保留** `persist_audio_attachment` 顶层：best-effort 持久化边界，已 `logger.exception`。
- **L112 ② 保留** `save_record_only`：best-effort DB 保存，已 `logger.exception`。
- **L122 ② 保留** `_cleanup_record_only`：cleanup 路径，已 `logger.exception`。

### 文件 4: voice/consumers.py

- **L84 ③ 补日志** disconnect 中 `group_discard` 的 `except: pass` → `logger.debug(exc_info=True)`。disconnect 路径保留宽捕获。
- **L98 ③ 补日志** disconnect 中 `_unregister_ambient_connection` 的 `except: pass` → `logger.debug`。
- **L104 ③ 补日志** disconnect 中 `VoicePipeline.cancel` 的 `except: pass` → `logger.debug`。
- **L140 ② 保留** `_send_json` 失败 → `self._closed=True`：WS 发送边界，「任何发送失败即视为连接关闭」是有意语义。保留。
- **L148 ② 保留** `_send_binary` 同 L140。保留。

### 文件 5: voice/consumer_session.py（4 处全保留）

- **L35 ② 保留** ASR `connect` 失败 → `return "connect"`：外部 ASR WS 连接，异常面杂（网络/协议/超时），返回错误码由上层处理；已 warning。
- **L41 ② 保留** ASR `configure` 失败 → disconnect + `return "configure"`；已 warning。
- **L121 ② 保留** 设备独占 force_disconnect 发送 `except: pass`（注释「旧连接可能已断开」）：handover cleanup。保留。
- **L290 ② 保留** 重连前旧 ASR client disconnect 的 `except`（"ignored"）：cleanup 路径，已 warning。

### 文件 6: graph/services/context_service.py

- **L59 ② 保留** `_llm_compress` 重试循环内兜底：**LLM 调用** `get_llm().ainvoke`。重试语义（捕获→warning→重试；耗尽 `return None` = 跳过压缩，安全降级）。红线核对：此处**不改变** LLM 恢复行为，降级不穿透；`ainvoke` 抛出的原始异常面杂，收窄反而可能漏捕导致中断。保留宽捕获并注明。
- **L81 ② 保留** `sse_callback("context_compacting")` 的 `except: pass`：SSE 进度信号 best-effort。保留。
- **L110 ② 保留** 创建 compaction 记忆失败 → warning：best-effort。保留（含日志）。
- **L114 ② 保留** `sse_callback("context_compacted")` 的 `except: pass`：同 L81。
- **L118 ① 收窄** finally 内 `lock.release()` 的 `except: pass`：Redis 分布式锁释放，异常面=`LockError`(已释放)/`RedisError`。改 `except (RedisError, LockError): pass`（`from redis.exceptions import RedisError, LockError`）。行为不变（释放失败忽略）。
- **L135 ② 保留** `build_context` 记忆召回失败 → warning：best-effort recall。保留（含日志）。

### 文件 7: graph/services/agent_service.py（6 处全保留）

- **L171 ② 保留** 顶层 agent 执行兜底，**L169 已 `except LLMException: raise`**（红线：LLM 异常已单独分类并传播）。此处捕获其余，已 `logger.exception` + content-control 处理。正确模式，保留。
- **L102 ② 保留** 上下文压缩预检 best-effort（降级=跳过压缩），已 warning。
- **L151 ② 保留** 流式循环内 monitor 推送 `except: pass`：实时流内 monitor 非关键，绝不可中断流。热路径保留 pass。
- **L201 ② 保留** finally 内 SSE rescue save，已 `logger.exception`。
- **L243 ② 保留** resume 生成顶层兜底 → 标记 FAILED + error chunk，已 `logger.exception`。
- **L253 ② 保留** finally 内 resume rescue save，已 `logger.exception`。

### 文件 8: graph/services/inference_service.py（Redis，收窄 4）

- **L25 ① 收窄** `get_active_task` → `return None`：`get_redis().get` + `InferenceTask.from_json`。改 `except (RedisError, ConnectionError, json.JSONDecodeError, ValueError) as e:`。保留 `logger.error`。
- **L43 ① 收窄** `register_task` → `return False`：`client.set`。改 `except (RedisError, ConnectionError) as e:`。
- **L60 ① 收窄** `complete_task` → `return False`：`client.get/delete` + from_json。改 `except (RedisError, ConnectionError, json.JSONDecodeError, ValueError) as e:`。
- **L86 ② 保留** `cancel_task` → `return False, None`：含 Redis **和** `channel_layer.send`，channel 层异常面不确定（ChannelFull 等），收窄会漏。保留宽捕获（安全降级）+ 已 `logger.error`。
- **L96 ① 收窄** `refresh_task_ttl` → `return False`：`client.expire`。改 `except (RedisError, ConnectionError) as e:`。

### 文件 9: graph/services/helpers/monitor.py

- **L21 ② 保留** Langfuse init → `return None`：SDK 初始化异常面杂，可观测性可选降级。已 warning。保留。
- **L52 ② 保留** `init_monitor` → `return None, None`：已 warning。
- **L72 ③ 补日志** tool 事件处理 → `return False`：**当前无日志**。保留宽捕获（非关键、实时），补 `logger.debug("monitor process failed (ignored)", exc_info=True)`。
- **L95 ② 保留** `push_final_monitor` → warning：已 warning。保留。

### 文件 10: graph/tools/memory.py

- **L16 ① 收窄** `_is_django_mode` `import django` → `return False`：异常面=`ImportError`/`AttributeError`。改 `except (ImportError, AttributeError):`。
- **L76 ② 保留** `mem_update` → `return f"更新失败: {e}"`：**LangChain 工具契约**——任何失败须返回字符串给 LLM，宽捕获是正确契约。保留。
- **L91 ② 保留** `mem_delete` → `return f"删除失败: {e}"`：同上，工具契约。保留。

### 需新增的 import（收窄涉及）

- `response_decision_service.py`：`from redis.exceptions import RedisError`
- `context_service.py`：`from redis.exceptions import RedisError, LockError`
- `inference_service.py`：`from redis.exceptions import RedisError`；`import json`（若未导入）
- graph/tools/memory.py、voice_pipeline.py、consumers.py、monitor.py：仅补日志/收窄内建异常，无新 import
（执行时须先 grep 确认各文件是否已导入，避免重复导入。）

## 4. 调查步骤

非 fix 类，无需诊断。分类依据已逐处列于 §3（文件:行 + 语义）。

## 5. 验证计划

### 5.1 自动化
- [ ] `source /home/dantsinghua/work/linchat/linchat/bin/activate`
- [ ] `pytest backend/tests/voice/ -q`（覆盖 response_decision / consumers / voice_pipeline / voice_session / persist）
- [ ] `pytest backend/tests/apps/graph/ -q`
- [ ] `pytest backend/tests/chat/test_context_service.py backend/tests/chat/test_inference_service.py backend/tests/chat/test_inference_cancel.py -q`
      （**注意**：context_service / inference_service / agent_service 的测试实际在 `backend/tests/chat/`，非 04-plan validation 写的 `backend/tests/apps/graph/`——见 §7）
- [ ] `pytest backend/tests/context/test_monitoring.py -q`（monitor.py）
- [ ] `ruff check backend/apps/voice/ backend/apps/graph/`
- [ ] 计数校验：改动后 10 文件 `grep -c 'except Exception'` 合计应为 **41**（原 49，收窄 8）
- [ ] `grep -rn 'except:'`（裸 except）在 10 文件中应为 0（不新增裸 except）

### 5.2 手动
- [ ] 无（本批不改运行时行为，纯类型收窄 + 日志；不进行 E2E）

### 5.3 性能
- [ ] 不适用（P2 tech-debt，无性能目标）

### 5.4 回归
- [ ] 跨 app 影响面：voice ← graph（inference/context）；跑 `pytest backend/tests/chat/ -q` 确认 chat↔graph 边界未受影响
- [ ] 全量抽查：`pytest backend/tests/ -q -x`（如时间允许）

## 6. 回滚策略

`git revert <commit>`（单 commit）。本批改动无 schema/migration/依赖变更，纯代码行级修改，revert 安全无副作用。
若仅个别文件出问题，可 `git checkout HEAD~1 -- <file>` 单文件回退后重测。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **目标达成度**：04-plan 期望 voice+graph「79→<35（缩减 50%+）」。实测 batch-04~21 后，本批 10 文件仅 49 处，其中 **34 处是顶层兜底/finally cleanup/事务补偿/WS 发送边界/LangChain 工具契约/LLM 重试降级**，按「行为等价·不为缩而缩」原则**必须保留**。诚实结论：本批只能收窄 8 处（→41），远达不到 50%。是否接受此现实目标？（强行凑数会破坏实时链路兜底，风险高）
- [ ] **response_decision L124 收窄置信度中等**：`_llm_classify` 尾部异常面为推断（HTTP/JSON/索引/转换）。若担心漏捕导致穿透中断，可改为**仅补日志、保留宽捕获**。请二选一。
- [ ] **inference_service L86（cancel_task）**：含 Redis + `channel_layer.send` 混合，channel 异常面不明，我判定保留。若你确认 channels 只抛 `ChannelFull`，可一并收窄。
- [ ] **04-plan validation 路径错误**：写的 `pytest backend/tests/apps/graph/` 不覆盖 context/inference/agent_service（它们的测试在 `backend/tests/chat/`）。已在 §5.1 修正，请知悉。
- [ ] **300 行硬限制**：`voice_pipeline.py`(309) 和 `consumer_session.py`(303) 超限，但本批对二者只做「补日志/保留」的轻改动。建议**不在本批拆分**（拆分需大幅扩 scope 且与 except 收窄目标无关），留待专项 batch。是否同意暂不拆？

## 8. 执行预算

- 预计 tool calls：~30（10 文件 × 逐处 Edit + 4 类测试运行 + 计数校验）
- 预计 token：中等（文件已在本 plan 定位到行，executor 无需大范围重读）
- 预计时间：1 session（与 estimated_sessions=1 一致，无需拆分）
