# Batch-23 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估（04-plan）：10 文件 / 150 行 / 1 session（实际 scope 为 9 文件）
> 依赖：无（depends_on=[]，满足；batch-22 已 COMPLETED）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）
> main HEAD：807dc8f（batch-22 已合入）

## 1. 任务理解（一句话）

沿用 batch-22 方法论，审计 media + memory + common(websocket_auth) + users 共 9 个文件的
`except Exception`，把**异常面单一已知**（纯 Redis / Cookie 解析）的处收窄为具体类型，给**静默
pass** 的边界处补 `logger`，其余**视图层顶层兜底 / Celery 任务顶层 / 事务补偿 / ES-Redis 副本容错
/ LLM 重试降级 / 顶层循环单条容错**的宽捕获一律保留并注明理由——只做「收窄类型 + 补日志」，
不改变任何一处「吞 vs 抛」的语义，不为凑数缩窄。

## 2. 涉及文件清单与改动预测

实测 9 个文件共 **39 处** `except Exception`（04-plan 描述的 media24/memory13/common15/users10=62
为 4 月旧清单，batch-04~22 后现状不同——见 §7）。均无裸 `except:`，无文件超 300 行硬限制
（最大 users/views.py=222，document.py=216）。

| # | 文件 | 行数 | 现 except | 收窄① | 补日志③ | 保留② | 精简潜力 |
|---|------|-----|----------|------|-------|------|---------|
| 1 | media/views.py | 119 | 5 | 0 | 5 | 0 | 中（5 处视图兜底 logger 无堆栈）|
| 2 | media/services/document_rag.py | 161 | 5 | 0 | 1 | 4 | 中（1 处 silent pass）|
| 3 | media/services/document_cache.py | 89 | 4 | 0 | 0 | 4 | 低（全 MinIO/PG 补偿）|
| 4 | media/services/document.py | 216 | 3 | 1 | 0 | 2 | 低 |
| 5 | media/tasks.py | 131 | 3 | 0 | 0 | 3 | 低（Celery 顶层/逐块）|
| 6 | memory/services.py | 149 | 6 | 0 | 1 | 5 | 中（1 处 silent pass）|
| 7 | memory/task_helpers.py | 93 | 5 | 0 | 0 | 5 | 低（全 best-effort/循环容错）|
| 8 | common/websocket_auth.py | 97 | 4 | 1 | 0 | 3 | 中 |
| 9 | users/views.py | 222 | 4 | 0 | 0 | 4 | 低（全已 logger.exception）|
| | **合计** | | **39** | **2** | **7** | **30** | |

改动后 grep `except Exception` 计数：39 → **37**（收窄 2 处替换为具体类型；补日志 7 处仍保留
`except Exception` 字面但增加可观测性）。诚实说明：绝大多数（30/39）属**必须保留**的边界兜底，
本批只能收窄 2 处——见 §7 目标达成度说明，不建议为凑数强缩。

## 3. 详细改动计划

标注：①=收窄  ②=保留(注明理由)  ③=补日志(保留宽捕获)

### 文件 1: media/views.py（5 处，全为 DRF 视图层顶层兜底，全 ③补日志）

5 处均在各自具体业务异常（`MediaUploadError` / `DocumentParseError`）单独 except 之后，作为**视图层
最外层兜底**返回统一 `ApiResponse.error`。红线核对：视图层顶层必须捕获一切以避免 500 裸抛，
**保留宽捕获正确**。当前 5 处均为 `logger.error(f"...error={e}")` **无堆栈**，可观测性差。

- **L33 ③** `upload_media` 兜底：`logger.error(f"媒体上传异常: user_id={user_id}, error={e}")` 加 `, exc_info=True`。
- **L53 ③** `get_media` 兜底：同上加 `exc_info=True`。
- **L84 ③** `parse_document` 兜底：同上加 `exc_info=True`。
- **L99 ③** `get_parse_task_status` 兜底：同上加 `exc_info=True`。
- **L117 ③** `get_parse_task_result` 兜底：同上加 `exc_info=True`。

### 文件 2: media/services/document_rag.py（4②+1③）

- **L72 ② 保留** keyword_search（PG 全文）best-effort：失败降级为空结果，已 `logger.warning`。异常面含 DB/查询层，保留。
- **L81 ② 保留** vector_search（EmbeddingClient httpx/openai + PG 向量）降级为 keyword：已 warning。异常面杂，保留。
- **L86 ② 保留** 语义模式下 keyword 二次兜底：已 warning。保留。
- **L127 ② 保留** fulltext 兜底：已 warning。保留。
- **L145 ③ 补日志** `att_map` 加载 `except Exception: pass`（**当前静默**）：批量取 MediaAttachment 用于填充文件名，
  失败时 att 缺失回退「未知文档」。保留宽捕获（best-effort 富化，不应中断结果），
  `pass` → `logger.warning("Doc RAG att_map load failed (degraded to 未知文档): user=%d, err=%s", user_id, e)`
  （需把 `except Exception:` 改为 `except Exception as e:`）。

### 文件 3: media/services/document_cache.py（4 处全保留 — MinIO/PG 原子性）

- **L25 ② 保留** MinIO download 缓存回退失败 → `return None`：best-effort 缓存读，已 warning。保留。
- **L46 ② 保留** MinIO upload 失败 → `return False`：写失败明确返回，已 `logger.error`。异常面=MinIO S3Error（类型不确定），保留。
- **L62 ② 保留** DB 更新失败 → **补偿删除 MinIO** → `return False`：**事务补偿模式**（PG-MinIO 原子性回滚），
  收窄会让部分异常跳过补偿删除导致 MinIO 孤儿对象。已 `logger.error`。**保留是正确模式**（notes 明确要求）。
- **L71 ② 保留** embedding 任务派发失败（非阻塞）→ `return True`：Celery dispatch best-effort，已 warning。保留。

### 文件 4: media/services/document.py（2②+1①）

- **L70 ② 保留** `_gateway_request` 尾部兜底：在 `DocumentParseError`（re-raise）与 `httpx.TimeoutException`
  单独处理之后，把其余一切网络/解析错误**翻译**为 `DocumentParseError(GATEWAY_ERROR)` 抛出（translate 模式）。
  已 `logger.error`。宽捕获是有意契约（任何未分类错误统一网关错误码），**保留**。
- **L155 ① 收窄** `parse_document` 写 Redis owner key 失败 → warning，非阻塞：**纯 Redis 单一调用**
  `redis_client.set(...)`。改 `except Exception as e:` → `except (RedisError, ConnectionError) as e:`
  （`from redis.exceptions import RedisError, ConnectionError`；ConnectionError 为 redis 自有，是 RedisError 子类，
  显式列出增强可读性）。行为不变（仍 warning 且不阻塞返回）。**中等置信** → 见 §7，可退为仅保留。
- **L183 ② 保留** `_poll_and_notify` 后台任务顶层兜底 → 发 failed 事件：**asyncio 后台任务顶层**，
  必须捕获一切避免任务静默死亡，已 `logger.error`。保留。

### 文件 5: media/tasks.py（3 处全保留 — Celery）

- **L92 ② 保留** 逐块 embedding 失败 → `embedding=None` 继续：**逐条循环容错**（单块失败不中断整批），已 warning。保留。
- **L109 ② 保留** embedding 生成顶层失败 → 更新状态 failed：**Celery 任务顶层兜底**，已 `logger.error`。保留。
- **L117 ② 保留** warmup 失败（非阻塞，归还 GPU）→ warning：best-effort，已 warning（warmup 内部 L28 亦有兜底）。保留。

### 文件 6: memory/services.py（5②+1③ — ES/Redis 副本容错，PG 唯一可信）

- **L64 ② 保留** `_dispatch_embedding` 派发失败 → 置 `EmbeddingStatus.FAILED` 补偿：Celery dispatch + 状态补偿，已 warning。保留。
- **L101 ② 保留** 向量检索失败降级为 keyword-only：**向量副本容错**（设计意图，PG keyword 兜底），已 warning。保留。
- **L106 ② 保留** keyword 检索失败 → 空 dict：检索副本容错，已 warning。保留。
- **L124 ③ 补日志** `summarize_and_store` 取现有记忆做上下文 `except Exception: pass`（**当前静默**）：
  best-effort 上下文增强，失败回退「无现有记忆」。保留宽捕获，
  `pass` → `logger.debug("Summarize existing-memories load failed (ignored): user=%d, err=%s", user_id, e)`
  （需 `except Exception:` → `except Exception as e:`）。
- **L137 ② 保留** LLM `ainvoke` 摘要重试循环内兜底：**LLM 调用重试**（红线：LLM 异常）——捕获→warning→重试，
  耗尽后 `return None` 安全降级。`ainvoke` 异常面杂，收窄可能漏捕中断重试。保留宽捕获并注明。
- **L143 ② 保留** 创建摘要记忆失败 → `return None`：best-effort 落库，已 warning。保留。

### 文件 7: memory/task_helpers.py（5 处全保留）

- **L28 ② 保留** `warmup_language_model` 顶层失败 → warning：best-effort 预热。保留。
- **L43 ② 保留** `collect_content` 取消息回退失败 → warning：best-effort 数据源回退。保留。
- **L55 ② 保留** 追加未识别说话人语音消息失败 → warning：best-effort 富化。保留。
- **L70 ② 保留** `run_summary` 查询消息活跃用户失败 → warning：best-effort union 数据源。保留。
- **L90 ② 保留** 逐用户摘要循环内单条失败 → warning：**顶层循环单条容错**（一个用户失败不中断整批 cron），已 warning。保留。

### 文件 8: common/websocket_auth.py（3②+1①）

- **L36 ② 保留** `__call__` 中 `_verify_token_async` 的通用兜底（在 `_WebSocketAuthError` 之后）→ 关闭 WS：
  **认证中间件顶层**，任何未预期错误须安全关闭连接，已 warning。宽捕获有意，保留。
- **L53 ① 收窄** `_extract_token_from_headers` 中 `cookie.load(cookie_str)` 失败 → `return None`：
  `http.cookies.SimpleCookie.load` 对畸形 Cookie 抛 `CookieError`。改 `except Exception:` →
  `except CookieError:`（`from http.cookies import CookieError`）。行为不变（解析失败当无 token），已 warning。**高置信**。
- **L62 ② 保留** `sm4_decrypt(token)` 失败 → raise `_WebSocketAuthError`（translate 模式）：任何解密失败即视为 Token 无效，
  宽捕获有意（sm4_decrypt 异常面不确定）。保留。
- **L92 ② 保留** `_close_websocket` 发送关闭帧失败 → debug：**发送/cleanup 边界**（连接可能已断），已 debug。保留。

### 文件 9: users/views.py（4 处全保留 — 已有 logger.exception）

4 处均为 DRF View 方法**顶层兜底**，位于各自具体异常（`AuthException` / `UsernameExistsError` /
`VoiceprintRegistrationError` / `ValueError`）单独 except 之后，且**均已 `logger.exception`（含堆栈）**，
可观测性已达标。视图层顶层必须捕获一切避免 500 裸抛，**全部保留，本文件无改动**。

- **L50 ② 保留** `CaptchaView.get` 兜底：已 `logger.exception`。
- **L100 ② 保留** `LoginView.post` 兜底：已 `logger.exception`。
- **L118 ② 保留** `LogoutView.post` 兜底（best-effort 登出）：已 `logger.exception`。
- **L217 ② 保留** `MemberListCreateView.post` 兜底：已 `logger.exception`。

### 需新增的 import（收窄涉及，共 2 处）

- `media/services/document.py`：`from redis.exceptions import RedisError, ConnectionError`（文件当前未导入，确认后新增）
- `common/websocket_auth.py`：`from http.cookies import CookieError`（当前仅 `from http.cookies import SimpleCookie`，可合并为 `from http.cookies import CookieError, SimpleCookie`）
（执行时先 grep 确认避免重复导入。document_rag.py / memory/services.py / media/views.py 仅补日志，无新 import。）

## 4. 调查步骤

非 fix 类，无需诊断。分类依据已逐处列于 §3（文件:行 + 语义）。

## 5. 验证计划

### 5.1 自动化
- [ ] `source /home/dantsinghua/work/linchat/linchat/bin/activate`
- [ ] `pytest backend/tests/media/ -q`（document_cache / document_rag / document_parse_service / chunk）
- [ ] `pytest backend/tests/chat/test_media_views.py backend/tests/chat/test_media_service.py backend/tests/chat/test_media_cleanup_task.py -q`（media/views.py、tasks.py 的实际测试在 tests/chat/ 下）
- [ ] `pytest backend/tests/memory/test_services.py backend/tests/memory/test_tasks.py -q`（memory/services.py、task_helpers.py）
- [ ] `pytest backend/tests/users/ -q`（websocket_auth 无独立测试，见 §7；users/views 本批无改动，跑回归确认）
- [ ] `ruff check backend/apps/media/ backend/apps/memory/ backend/apps/common/websocket_auth.py backend/apps/users/views.py`
- [ ] 计数校验：改动后 9 文件 `grep -c 'except Exception'` 合计应为 **37**（原 39，收窄 2）
- [ ] `grep -rn 'except:'` 9 文件中应为 0（不新增裸 except）

### 5.2 手动
- [ ] 无（本批不改运行时行为，纯类型收窄 + 日志；不进行 E2E）

### 5.3 性能
- [ ] 不适用（P2 tech-debt，无性能目标）

### 5.4 回归
- [ ] 跨 app 影响面：media ← memory(EmbeddingClient)；memory ← chat(Message)；跑
      `pytest backend/tests/memory/ backend/tests/media/ -q` 确认边界
- [ ] 全量抽查（时间允许）：`pytest backend/tests/ -q`

## 6. 回滚策略

`git revert <commit>`（单 commit）。本批无 schema/migration/依赖变更，纯代码行级修改，revert 安全无副作用。
若个别文件出问题，可 `git checkout HEAD~1 -- <file>` 单文件回退后重测。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **目标达成度（重要）**：04-plan 描述期望「62 处缩减到 ~25 处」、metrics 期望「全项目 143→<60」。
      现实：① 这 9 文件实测仅 **39 处**（非 62，4 月旧清单已过时）；② 全项目当前 **146 处**（非 143，
      batch-22 后其它模块新增），本批仅动这 9 文件、收窄 2 处 → 全项目变 144，**远达不到 <60**；
      ③ 39 处中 30 处属视图顶层兜底/Celery 任务顶层/事务补偿/ES-Redis 副本容错/LLM 重试/顶层循环容错，
      按「行为等价·不为缩而缩」**必须保留**。诚实结论：本批只能收窄 2 处（→37）+ 补日志 7 处。
      **是否接受此现实目标？**（强行凑数会破坏原子性补偿与实时容错，风险高）
- [ ] **document.py L155 收窄置信中等**：`redis_client.set` 纯 Redis 调用，判定收窄为 `(RedisError, ConnectionError)`。
      若担心 get_redis 连接层抛非 RedisError 变体，可退为**仅保留宽捕获**（该处已有 warning）。请二选一。
- [ ] **04-plan validation 路径**：media/views.py、tasks.py 的测试实际在 `backend/tests/chat/`（test_media_views/
      test_media_service/test_media_cleanup_task），非 `backend/tests/media/`。已在 §5.1 修正，请知悉。
- [ ] **websocket_auth.py 无专属单测**：`backend/tests/` 下未见 websocket_auth 测试文件。L53 收窄（CookieError）
      与 L36/L62/L92 保留均不改行为，风险低；但缺乏直接覆盖。是否接受仅靠 ruff + users 回归验证？
- [ ] **scope 文件数**：04-plan title 写「10 文件」但 files_touched 仅列 9 个（无 memory/tasks.py？实际 media/tasks.py 已含）。
      按 files_touched 的 9 文件执行。确认无遗漏文件。

## 8. 执行预算

- 预计 tool calls：~18（9 文件中仅 6 文件需 Edit：media/views ×5、document_rag ×1、document ×1+import、
  memory/services ×1、websocket_auth ×1+import；users/views 与 3 个纯保留文件无改动 + 4 类测试 + 计数校验）
- 预计 token：中等（文件已逐处定位到行，executor 无需大范围重读）
- 预计时间：1 session（与 estimated_sessions=1 一致，无需拆分）
