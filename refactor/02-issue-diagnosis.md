# LinChat 运行时问题诊断（Phase 1）

> 生成时间：2026-04-16 16:40
> 数据范围：见下表，全部为 2026-04-16 当日日志
> 先验输入：docs/legacy-and-debts.md

## 执行摘要

- 日志总行数：**2,273**，时间范围：2026-04-16 11:33 ~ 16:31
- ERROR 级别总数：**3**（Speaker identify timeout ×1、ASGI Exception ×1、ASR reconnect failed ×1）
- WARNING 级别总数：**99**（LLM decision JSONDecodeError ×6、TTS WS 1006 ×19、rate limit ×137 等）
- Top 3 错误模式：(1) 声纹识别 100% 失败，(2) LLM 意图分类超时/解析失败，(3) ASR WebSocket 断连
- 端到端语音延迟 P50=**10.8s**，P95=**19.5s**（SLO 5s，**全部超标**）
- Trace ID 贯穿率：仅 `request_id` 出现 40 次，无跨服务 `trace_id`
- TTS 回声过滤：代码存在但日志 **0 次触发**

## 1. 日志清单

| 文件 | 大小 | 行数 | 时间范围 | 说明 |
|------|------|------|----------|------|
| `/tmp/linchat-backend.log` | 175K | 1,814 | 11:33~16:26 | 主日志，含语音/HTTP 全部 |
| `/tmp/linchat-celery-worker.log` | 41K | 308 | 11:33~16:31 | Worker 全部 INFO，0 ERROR |
| `/tmp/linchat-celery-beat.log` | 18K | 141 | 11:33~16:31 | 调度日志，正常 |
| `/tmp/linchat-frontend.log` | 274B | 10 | 11:33 | 仅启动信息 |

## 2. 错误模式 Top 20

### 2.1 按异常类型聚合

| # | 异常类型 | 频次 | 首次出现 | 最近出现 | 代表性位置 | 推测根因 |
|---|---------|------|---------|---------|-----------|---------|
| 1 | JSONDecodeError | 6 | 14:51:49 | 15:48:57 | `response_decision_service.py:93` | LLM 返回截断 JSON（`json_object` mode 未生效或 max_tokens=100 截断） |
| 2 | ConnectionClosedError | 1 | 16:26:11 | 16:26:11 | `ws_client_base.py:61` → `asr_stream_client.py:31` | ASR WS keepalive ping timeout，Gateway 侧超时断开 |
| 3 | TimeoutError | 1 | 16:26:11 | 16:26:11 | uvicorn WS 层 `websockets_impl.py:244` | ASR 断连后关闭 WS 超时 |

### 2.2 按日志级别聚合（WARNING 模式）

| # | 模式 | 频次 | 代表性位置 | 说明 |
|---|------|------|-----------|------|
| 1 | Too Many Requests (429) | 137 | uvicorn HTTP 层（media endpoint） | 前端批量加载 30+ 媒体附件触发限流 |
| 2 | TTS WS closed code=1006 | 19 | `tts_stream_client.py` (BaseWSClient) | TTS 每次播报完正常关闭但 code=1006（非优雅关闭） |
| 3 | LLM decision error + JSONDecodeError | 6 | `response_decision_service.py:93~103` | LLM 意图分类返回无效 JSON |
| 4 | LLM intent classify timeout | 122 | `response_decision_service.py:100` | 意图分类 httpx 超时（降级 RECORD_ONLY） |
| 5 | ASR connect/reconnect failed | 3+1 | `consumer_session.py` | ASR WS 握手超时，3 次重连全部失败 |
| 6 | StreamingHttpResponse synchronous iterator | 2 | Django `response.py:521` | SSE 视图使用同步迭代器 |

## 3. LLM/Agent 错误专项

### 3.1 LLM 意图分类（ResponseDecisionService）

| 指标 | 值 | 说明 |
|------|---|------|
| 总调用次数（推算） | ~313（185 ambient records + 122 timeouts + 6 errors） | 无精确计数 |
| 超时次数 | **122**（39%） | 超时降级为 RECORD_ONLY，用户无感 |
| JSON 解析失败 | **6**（2%） | `_classify_intent_llm` 返回 None → 穿透到规则链 |
| 成功率 | ~59% | |

**根因分析**：
- 超时（122 次）：`VOICE_DECISION_LLM_TIMEOUT` 设定值可能偏小，或 LLM Gateway 延迟不稳定
  - 代码位置：`response_decision_service.py:86`（httpx.AsyncClient timeout 参数）
- JSON 解析失败（6 次）：LLM 返回 `"Unterminated string starting at: line 1 column 13"`
  - 代码位置：`response_decision_service.py:93`（`json_module.loads`）
  - 推测：`max_tokens=100`（line 91）对短文本足够，但 LLM 偶尔输出非 JSON 格式或被截断
  - **安全降级有效**：line 102-104，`except Exception` 返回 None → 穿透到规则链决策

### 3.2 Agent 推理（VoicePipeline）

- 20 次完整 Pipeline 执行，**0 次 Agent 推理失败**
- LLM 推理耗时见第 4 节

### 3.3 SubAgent / 工具调用失败

- 日志中 **无** SubAgent 工具调用失败记录（ambient 模式不触发 SubAgent）

## 4. 慢请求分析

### 4.1 端到端语音 Pipeline 延迟（20 个完整样本）

| 阶段 | min | P50 | P95 | max | 占比(P50) |
|------|-----|-----|-----|-----|----------|
| LLM 推理 | 3,457ms | 6,342ms | 12,880ms | 12,880ms | **59%** |
| TTS WS 连接 | 998ms | 1,054ms | 1,788ms | 1,788ms | 10% |
| TTS 合成 | 887ms | 2,221ms | 6,275ms | 6,275ms | 20% |
| HA 播报下发 | 292ms | 358ms | 414ms | 414ms | 3% |
| **端到端总计** | **6,689ms** | **10,828ms** | **19,546ms** | **19,546ms** | **100%** |

**结论**：5s SLO **全部 20 次均超标**，最短 6.7s，最长 19.5s。

**瓶颈定位**：
1. **LLM 推理（P50=6.3s，占 59%）**：`voice_pipeline.py` → `AgentService.execute()` → LLM Gateway
   - 代码路径：`voice_pipeline.py` 中 `AgentService.execute()` 调用
   - 推测：ambient 模式全量 Agent 流程（含 PromptBuilder 记忆召回），对短回复场景过重
2. **TTS 连接建立（P50=1.05s，恒定开销）**：每次 Pipeline 新建 TTS WS 连接
   - 代码路径：`tts_pipeline_manager.py` → `TTSStreamClient.connect()`
   - 可优化：连接复用或预连接
3. **TTS 合成（P50=2.2s）**：取决于回复文本长度
   - P95=6.3s 对应较长回复（如 line 1076 中的长文本）

### 4.2 HTTP API 延迟

- 日志中无 `duration_ms` 字段（uvicorn 不打印请求耗时），**无数据**

## 5. 可观测性缺口

### 5.1 Trace ID 贯穿率

| 标识符 | 出现次数 | 出现位置 | 说明 |
|--------|---------|---------|------|
| `request_id` | 40 | Pipeline launch / 注册推理任务 / 完成推理任务（20 个 Pipeline ×2） | 仅覆盖推理任务注册/完成 |
| `trace_id` | 0 | — | **完全缺失** |
| `X-Request-ID` | 0 | — | 无 HTTP 层 trace |
| `correlation` | 0 | — | 无跨服务关联 |

**结论**：`request_id` 仅在 `VoicePipeline` 内部 `InferenceService.register_task()` / `complete_task()` 使用，**不贯穿以下环节**：
- ASR 转录 → 聚合 → 决策 → Pipeline 入口（无法关联"哪句话触发了哪次推理"）
- Agent 内部（LangGraph execution）→ TTS → HA 播报（无法端到端追踪）
- HTTP API 层（完全无 trace）

### 5.2 日志格式不统一

| 来源 | 格式 | 示例 |
|------|------|------|
| 应用代码 | `LEVEL YYYY-MM-DD HH:MM:SS,ms message` | `INFO 2026-04-16 14:51:45,688 LLM intent classify timeout` |
| uvicorn | `LEVEL: message` 或 `INFO: IP - "METHOD path" status` | `INFO: 74.211.99.72:0 - "GET /api/v1/auth/me HTTP/1.1" 200 OK` |
| Django | `WARNING YYYY-MM-DD HH:MM:SS,ms message: path` | `WARNING 2026-04-16 16:09:09,476 Unauthorized: /api/v1/auth/me` |

三种格式混合，无法统一 grep 或结构化解析。

## 6. 沉默失败

### 6.1 代码中的 `except Exception` 吞异常

| 文件:行 | 吞掉的异常 | 影响 |
|---------|-----------|------|
| `response_decision_service.py:102-104` | LLM 分类任何异常 → 返回 None | 穿透到规则链，不影响功能但丢失诊断信息 |
| `response_decision_service.py:123` | `_fetch_intent_context` 记忆召回失败 | 降级为无上下文分类，静默 |
| `response_decision_service.py:128` | `_fetch_intent_context` 异常 | 同上 |
| `response_decision_service.py:158` | `_load_wake_words` DB 查询失败 → 返回默认 | 唤醒词判断失效 |
| `response_decision_service.py:168` | `_get_recent_speaker_count` Redis 失败 → 返回 0 | 多 speaker 检测失效 |
| `response_decision_service.py:201` | `_is_tts_echo` Redis 失败 → 返回 False | TTS echo 检测失效 |
| `consumer_events.py:124` | Speaker identify 任何异常 → 返回 None | 声纹识别异常被吞，fallback 到无 speaker |
| `consumer_session.py:27,33` | ASR 连接异常 | 被 `_handle_asr_failure()` 处理 |
| `consumer_session.py:116` | ASR 重连异常 | 重连失败静默 |
| `consumer_inference.py:44` | Pipeline 执行异常 | `exc_info=True` 有日志但不传播 |
| `speaker_service.py:47` | `_retrospective_match` 异常 | 历史匹配失败静默，`logger.exception` 有日志 |

### 6.2 TTS 回声过滤：存在但未生效

代码实现完整（`response_decision_service.py:171-203`），含两级检测：
1. Redis `voice:tts_playing:{user_id}` 键存在检查
2. `voice:tts_history:{user_id}` 历史文本相似度匹配（SequenceMatcher ratio > 0.7）

但日志中 **0 次 TTS echo detected**（debug 级别）。可能原因：
- **HA 音箱输出场景**：TTS 通过 HA 小爱音箱播放，reSpeaker 麦克风可能未拾取到回声（物理距离/音量）
- 或 `voice:tts_playing` 键 TTL=30s 已过期
- 或 `voice:tts_history` 未被正确写入（需检查 `tts_router.py` 是否调用 `RPUSH` 写入历史）

## 7. 资源告警

### 7.1 连接 / 超时

| 事件 | 频次 | 代码位置 | 说明 |
|------|------|---------|------|
| ASR WS ping timeout 断连 | 1 | `ws_client_base.py` | Gateway ASR WS keepalive 超时（16:26:11） |
| ASR 握手超时 | 3 | `consumer_session.py` `_reconnect_asr()` | 重连 3 次均超时，间隔 2s×3=12s |
| ASR 重连最终失败 | 1 | `consumer_session.py` | 3 次失败后放弃，该 user 语音服务中断 |
| Speaker identify timeout | 1 | `speaker_service.py:103` | Gateway 声纹 API 10s 超时 |

### 7.2 频率限制

| 事件 | 频次 | 说明 |
|------|------|------|
| HTTP 429 Too Many Requests | 137 | 前端加载历史消息时批量请求 media 附件 |

### 7.3 WebSocket 非优雅关闭

| 事件 | 频次 | 说明 |
|------|------|------|
| TTS WS closed code=1006 | 19 | 每次 TTS 播报完后 WS 关闭，但 code=1006（异常关闭而非 1000 正常关闭） |
| ASR WS closed code=1006 | 1 | ASR 连接 ping timeout 导致 |

TTS code=1006 频率 = 19/20 Pipeline（95%），说明 TTS WS 关闭流程未发送 close frame。
代码位置：`tts_stream_client.py` → `BaseWSClient.disconnect()` → `ws_client_base.py`

### 7.4 Celery 任务

- **0 失败**，**0 堆积**，5 个定时任务全部按时执行
- Embedding health check: `retried=0, stuck_pending=0, stuck_processing=0, total_failed=0`
- 所有任务执行时间 < 40ms

## 8. 综合问题清单（按优先级）

| # | 问题 | 频次 | 影响范围 | 对应代码 | 对应 legacy 条目 | 优先级 |
|---|------|------|---------|---------|---------------|--------|
| 1 | **声纹识别 100% 失败** — 219/219 次 identified=False，conf 中位数 0.08 | 219/219 | 017 核心功能完全失效 | `speaker_service.py:69-107`，Gateway `/v1/voice/speakers/identify` | 二·B P0 Day-1 | **P0 fix** |
| 2 | **端到端语音延迟 P50=10.8s** — LLM 推理占 59%（P50=6.3s） | 20/20 超 SLO | 用户体验核心指标 | `voice_pipeline.py` → `AgentService.execute()` | 二·性能 P1 | **P1** |
| 3 | **LLM 意图分类超时率 39%** — 122/~313 次超时降级 | 122 | ambient 决策准确性降低 | `response_decision_service.py:86-101` | 无（新发现） | **P1** |
| 4 | **LLM 意图分类 JSON 解析失败** — 6 次 Unterminated string | 6 | 决策穿透到规则链 | `response_decision_service.py:91,93` | 无（新发现） | P2 |
| 5 | **TTS WS 非优雅关闭 code=1006** — 19/20 次 | 19/20 | 潜在资源泄漏 | `tts_stream_client.py` / `ws_client_base.py` | 无（新发现） | P2 |
| 6 | **ASR WS 断连后重连失败** — 3 次握手超时，服务中断 | 1 事件 | 单用户语音中断 | `consumer_session.py` `_reconnect_asr()` | 二·性能 | P2 |
| 7 | **TTS 回声过滤未生效** — 代码存在但 0 次触发 | 0/185 | HA 音箱场景回声风险 | `response_decision_service.py:171-203` | 无（新发现） | P2 |
| 8 | **Trace ID 完全缺失** — 无法跨服务追踪 | 全局 | 排障效率 | 全局 | 六·P0 可观测性 | **P0 obs** |
| 9 | **日志格式三种混合** — 无法结构化解析 | 全局 | 监控/告警 | 全局 | 六·P0 可观测性 | P0 obs |
| 10 | **HTTP 429 媒体限流** — 前端批量请求 137 次 429 | 137 | 页面加载体验 | `rate_limiter.py` / 前端 MessageList | 无（新发现） | P3 |
| 11 | **13 个测试失败** — 详见 8.1 | 13/1586 | CI 红色 | 见下表 | 四·测试 P0 Day-1 | **P0 fix** |
| 12 | **unknown speaker 标签无限增长** — 已达 unknown_231 | 持续 | Redis 内存 + 无意义标签 | `consumer_events.py:128-139` | 无（新发现） | P3 |

### 8.1 失败测试根因分析

| 测试文件 | 失败数 | 根因 | 修复方向 |
|---------|--------|------|---------|
| `tests/chat/test_media_cleanup_task.py` | 8 | 测试断言 `cleaned==2` 但实际清理了 6 条 — 测试未隔离数据库（`--reuse-db` 导致残留数据） | 测试 setup 清理残留或使用 TransactionTestCase |
| `tests/memory/test_models.py` | 1 | `UserMemoryEmbedding.objects.count()==1` 断言失败（实际 6）— 同样数据库残留 | 同上 |
| `tests/memory/test_tasks.py` | 3 | `summarize_and_store` 被调用 2 次（expected 1）— 数据库中存在真实用户（user_id=7）的数据，月度总结对两个用户都触发 | 测试 mock 需过滤 `SysUser.objects.filter()` |
| `tests/apps/graph/test_document_agent.py` | 1 | 断言 `"部分解析" in result` 失败 — 实际返回 `"解析超时（900秒）"` | 测试断言需更新以匹配新的超时消息格式 |

## 9. Open Questions

1. **Q1**：声纹识别 conf 分布 [-0.25, 0.49]（中位 0.08），是 Gateway 模型质量问题还是注册样本不足？需查 Gateway 侧 `SpeakerProfile` 数量和质量评分。
2. **Q2**：LLM 意图分类 39% 超时率，`VOICE_DECISION_LLM_TIMEOUT` 当前值是多少？是否需要调大或改为异步非阻塞？
3. **Q3**：TTS WS code=1006 是 Gateway 侧主动断开还是客户端未发 close frame？需对比 `ws_client_base.py:disconnect()` 逻辑。
4. **Q4**：TTS 回声检测的 `voice:tts_history:{user_id}` 是否被写入？`tts_router.py` 中 `send_to_ha_speaker()` 是否写入该 key？
5. **Q5**：`unknown_counter` Redis 键无 TTL，全局递增，长期运行后标签编号会无限增长，是否需要定期清理？

## 10. 数据限制说明

| 限制项 | 影响 |
|--------|------|
| **日志仅 ~5 小时**（11:33~16:31） | 无法评估日/周趋势、定时任务（daily_summary/monthly_summary）执行情况 |
| **前端日志仅启动信息**（10 行） | 前端运行时错误完全无数据 |
| **无 HTTP 请求耗时** | uvicorn 默认不打印 `duration_ms`，API P95/P99 无法计算 |
| **无 Langfuse trace 导出** | Agent 内部 SubAgent/工具调用延迟无法分解 |
| **DEBUG 日志可能被过滤** | TTS echo 检测使用 `logger.debug`，可能因日志级别设为 INFO 而不显示 |
| **仅 ambient 模式数据** | voice_chat 模式无运行数据，无法对比 |
| **Docker 容器日志未采集** | PostgreSQL/Redis 慢查询无数据 |
