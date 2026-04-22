# Batch 02 执行计划

> 生成时间：2026-04-17
> 类型：fix | 优先级：P0-Day1 | 风险：low（测试） / 中（ASR 竞态）
> 预估：4 文件 / 120 行 / 1 session（建议拆 2 次 commit）
> 依赖：无（depends_on=[]，已满足）
> SLO 影响：无（但 ASR 重连竞态会间接影响 5s 端到端 SLO）

## 1. 任务理解（一句话）

修复 P0 Day-1 卡 CI 的 9 个测试（测试间数据库隔离破裂 + run_summary 未隔离 user_id），并修复生产语音 ASR 周期性断连后的"双重重连"竞态（旧会话未完全关闭就建新会话）。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/tests/chat/test_media_cleanup_task.py | 227 | +6 -1 | 增加 setUp 显式清理 | 低 | 低 |
| 2 | backend/tests/memory/test_models.py | 111 | +6 -2 | 拆分两个 TestCase 类的隔离 | 低 | 低 |
| 3 | backend/tests/memory/test_tasks.py | 508 | +30 -5 | mock active 用户集合 + Message 过滤 | 低 | 中（508 行接近 600 行注意） |
| 4 | backend/apps/voice/consumer_session.py | 269 | +25 -8 | 重连去重锁 + 断开/连接顺序 | 中 | 低 |
| **合计** | | **1115** | **+67 -16** | | | |

第 4 项预计 30 行不足，含日志增强后总改动约 80-90 行，仍在 120 行预算内。

## 3. 详细改动计划

---

### 文件 1：backend/tests/chat/test_media_cleanup_task.py

#### 改动 1.1：在 TestCase 中加入 setUp 强制清理 MediaAttachment

- 位置：第 26-27 行，`class TestCleanExpiredMedia(TestCase):` 之后
- 当前代码：
  ```python
  class TestCleanExpiredMedia(TestCase):
      """清理过期媒体文件 Celery 任务测试"""

      def _create_attachment(
  ```
- 改动方案：
  ```python
  class TestCleanExpiredMedia(TestCase):
      """清理过期媒体文件 Celery 任务测试"""

      def setUp(self) -> None:
          """每个测试前清理 MediaAttachment，防止 --reuse-db 跨测试残留"""
          super().setUp()
          MediaAttachment.objects.all().delete()

      def _create_attachment(
  ```
- 改动理由：
  - `TestCase` 默认靠事务回滚隔离，但 `--reuse-db`（pytest.ini:6）模式下若上一次 pytest 进程异常退出，残留行会保留到下次。
  - 02-issue-diagnosis.md 8.1 节确认：`cleaned==2` 期望 vs `cleaned==6` 实际，多出 4 条来自历史残留。
  - `setUp` + `delete()` 是最小侵入的修法，不改 fixture 模式（保持团队习惯一致）。
- 备选方案：改用 `TransactionTestCase`——更彻底但慢 5-10 倍。**不采用**。
- 预估行数：+5 -1（保留空行）

---

### 文件 2：backend/tests/memory/test_models.py

#### 改动 2.1：在两个 TestCase 加 setUp 清理（第 14、68 行）

- 位置 1：第 14-15 行 `class TestUserMemoryModel(TestCase):`
- 位置 2：第 68-69 行 `class TestUserMemoryEmbedding(TestCase):`
- 改动方案（两处对称）：
  ```python
  class TestUserMemoryModel(TestCase):
      """UserMemory 模型测试"""

      def setUp(self) -> None:
          super().setUp()
          UserMemoryEmbedding.objects.all().delete()
          UserMemory.objects.all().delete()

      def test_create_with_defaults(self) -> None:
          ...
  ```
  ```python
  class TestUserMemoryEmbedding(TestCase):
      """UserMemoryEmbedding 模型测试"""

      def setUp(self) -> None:
          super().setUp()
          UserMemoryEmbedding.objects.all().delete()
          UserMemory.objects.all().delete()

      def test_create_embedding(self) -> None:
          ...
  ```
- 改动理由：
  - 失败测试为 `test_cascade_delete`（第 88 行），断言 `UserMemoryEmbedding.objects.count() == 1` 但实际为 6，与 02-issue-diagnosis.md 一致。
  - 两个 TestCase 都创建 `UserMemory + UserMemoryEmbedding`，必须按 FK 顺序（先 Embedding 后 Memory）清理避免 ProtectedError。
  - 与 test_tasks.py 中的 `pytest.fixture cleanup` 模式保持目的一致，但因为这里用 `django.test.TestCase`（不是 pytest 类），必须用 `setUp`。
- 预估行数：+8 -2

---

### 文件 3：backend/tests/memory/test_tasks.py

#### 改动 3.1：TestGenerateDailySummary cleanup fixture 增加 SysUser 过滤 / mock

- 位置：第 342-349 行，`class TestGenerateDailySummary` 的 cleanup fixture
- 当前代码：
  ```python
  class TestGenerateDailySummary:
      @pytest.fixture(autouse=True)
      def cleanup(self):
          UserMemoryEmbedding.objects.all().delete()
          UserMemory.objects.all().delete()
          yield
  ```
- 根因分析（重要）：
  - 02-issue-diagnosis.md 8.1 节："`summarize_and_store` 被调用 2 次（expected 1）"
  - 实际链路：`generate_daily_summary` → `run_summary()`（task_helpers.py:60-93）
  - **关键代码 task_helpers.py:69**：
    ```python
    active.update(Message.objects.filter(created_time__gte=start, created_time__lt=end).values_list("user_id", flat=True).distinct())
    ```
    这一行**会把数据库里所有用户**（包括 SysUser=7 即 dantsinghua 的真实 Message）一并加入 active 集合，导致除 `user_id=1` 外还有真实用户被总结。
  - cleanup fixture 只清理了 `UserMemory`，没清 `Message` 表。

- 改动方案（首选 — patch run_summary 内部 active 收集）：
  ```python
  class TestGenerateDailySummary:
      @pytest.fixture(autouse=True)
      def cleanup(self):
          from apps.chat.models import Message
          UserMemoryEmbedding.objects.all().delete()
          UserMemory.objects.all().delete()
          # 清理 Message 表，否则 task_helpers.run_summary() 会把真实用户也加入 active 集合
          Message.objects.all().delete()
          yield
  ```
- 改动理由：
  - 不改 task_helpers.py 业务代码（业务上"扫所有 message 用户"是正确逻辑）。
  - 测试侧补齐 Message 清理，恢复隔离。
- 备选方案：在测试里 `@patch("apps.memory.task_helpers.Message.objects.filter")` 让其返回 `EmptyQuerySet`。**不采用**——会绑死 task_helpers 的内部实现路径。

#### 改动 3.2：TestGenerateMonthlySummary cleanup 同样修复

- 位置：第 424-431 行，`class TestGenerateMonthlySummary` 的 cleanup fixture
- 改动方案：与 3.1 完全对称，加入 `Message.objects.all().delete()`
- 预估行数：+4 -0

#### 改动 3.3（可选 — 仅在 3.1/3.2 不够时启用）：TestGenerateEmbeddingActiveUsers / TestRetryFailedEmbeddings 也补齐 Message 清理

- 这两个类的 cleanup 只清 UserMemory*，但不调用 `run_summary`，理论上不需要。
- **本次先不动**，跑完 3.1+3.2 再看。

#### 改动 3.4：诊断步骤（在执行前确认）

- [ ] H-A：执行 `psql -c "SELECT user_id, count(*) FROM message GROUP BY user_id"` 确认数据库里有 user_id=7 的真实数据
- [ ] H-B：在 `run_summary` 加临时 print 确认 active 集合实际值

预估行数合计：+4 -0（3.1）+ +4 -0（3.2）= **+8 -0**

---

### 文件 4：backend/apps/voice/consumer_session.py

#### 背景：从日志中确认的竞态时序

```
21:36:03,591 ASR WS closed code=1006
21:36:03,592 ASR error: code=CONNECTION_CLOSED   ← _on_asr_error 触发 → 调用 _reconnect_asr (1)
21:36:03,609 Voice session closed: user_id=7
21:36:05,594 ASR connect failed: Errno 111      ← reconnect 第 1 次失败
21:36:08,638 ASR WS connected: ...               ← reconnect 第 2 次成功
21:36:08,642 ASR reconnected: user=7

22:37:31,026 ASR reconnected: user=7              ← 重连成功（第 1 次触发）
22:37:31,354 ASR reconnected: user=7              ← 重连又成功了（第 2 次触发，328ms 后）
                                                   ↑ 同一秒两个 reconnect 完成 = 旧会话未关就建新的

22:27:29,343 ASR reconnect failed (3 attempts)   ← 同一秒 3 次重连失败
22:27:29,343 ASR reconnect failed (3 attempts)
22:27:29,344 ASR reconnect failed (3 attempts)
                                                   ↑ 3 个并发重连任务在跑
```

竞态来源：
- `consumer_events.py:175`：`_on_asr_error` 在 ASR error 事件触发时调用 `await self._reconnect_asr()`
- `ws_client_base.py:74-76`：`_receive_loop` 异常分支会调用 `_on_error` 但**不直接触发重连**（应该）。
- 但**真实问题**：`_on_vad_speech_*` / `_handle_audio_frame` 在 ASR 断开后仍可能触发其它路径间接调用 `_reconnect_asr`；且 ASR Gateway 1012 重启时可能在短时间内连发多个 error 事件。

#### 改动 4.1：增加 `_reconnect_lock` 去重并发重连

- 位置：第 257-269 行 `_reconnect_asr` 方法
- 当前代码：
  ```python
  async def _reconnect_asr(self) -> None:
      if getattr(self, "_mode", None) != "ambient":
          return
      for attempt in range(1, 4):
          await asyncio.sleep(2)
          asr_err = await self._connect_and_configure_asr()
          if not asr_err:
              await voice_session_service.update_session(
                  self.user_id, upstream_connected=True, asr_session_id=self._asr_client.session_id)
              logger.info("ASR reconnected: user=%s", self.user_id)
              return
      logger.error("ASR reconnect failed after 3 attempts: user=%s", self.user_id)
      await self._send_error("ASR_RECONNECT_FAILED", "语音服务重连失败，请重新连接", recoverable=False)
  ```
- 改动方案：
  ```python
  async def _reconnect_asr(self) -> None:
      if getattr(self, "_mode", None) != "ambient":
          return
      # 去重锁：防止 _on_asr_error / 心跳超时 / ASR Gateway 重启同时触发多个重连任务
      lock = getattr(self, "_reconnect_lock", None)
      if lock is None:
          lock = asyncio.Lock()
          self._reconnect_lock = lock
      if lock.locked():
          logger.info("ASR reconnect already in progress, skip duplicate trigger: user=%s", self.user_id)
          return
      async with lock:
          # 关键：先彻底关闭旧 ASR client，再尝试建新连接
          if self._asr_client:
              try:
                  await self._asr_client.disconnect()
              except Exception as e:
                  logger.warning("ASR old client disconnect error (ignored): user=%s, err=%s", self.user_id, e)
              self._asr_client = None
          for attempt in range(1, 4):
              await asyncio.sleep(2)
              asr_err = await self._connect_and_configure_asr()
              if not asr_err:
                  await voice_session_service.update_session(
                      self.user_id, upstream_connected=True, asr_session_id=self._asr_client.session_id)
                  logger.info("ASR reconnected: user=%s, attempt=%d", self.user_id, attempt)
                  return
              logger.warning("ASR reconnect attempt %d/3 failed: user=%s, err=%s", attempt, self.user_id, asr_err)
          logger.error("ASR reconnect failed after 3 attempts: user=%s", self.user_id)
          await self._send_error("ASR_RECONNECT_FAILED", "语音服务重连失败，请重新连接", recoverable=False)
  ```
- 改动理由（按问题列）：
  1. **`if lock.locked(): return`**：防止重复触发——日志中 22:37:31 同秒两次成功重连的根因。
  2. **显式 `disconnect()` 旧 client + 置 None**：保证旧 ASR session 完全释放再建新的。`_connect_and_configure_asr` 第 22-23 行已有判断 "如果 connected 才 disconnect"，但 `connected=False` 状态下 `_recv_task` 和 `_ws` 可能仍持有资源未清理。`cleanup_ws_connection` 是幂等的，多调一次安全。
  3. **每次 attempt 失败也打 warning 日志**：当前只有最终 ERROR，无法看到 3 次的具体失败原因。
- **不修改部分**：`_connect_and_configure_asr` 内部逻辑——已有 disconnect 老连接的判断（line 22），但只在 `_asr_client.connected==True` 时 disconnect。本次改动在外层显式置 None，绕过这个 connected 判断的潜在漏洞。

#### 改动 4.2：诊断步骤（执行前确认是否还需要扩大改动）

- [ ] D-A：阅读 `consumers.py` 中是否有其他路径调用 `_reconnect_asr`（除 `consumer_events.py:175` 之外）
  ```bash
  rg "_reconnect_asr" backend/apps/voice/
  ```
  当前已确认只有 1 处（`consumer_events.py:175`），但仍建议执行前再 grep 一次以防遗漏。
- [ ] D-B：观察 22:02:51 / 22:37:31 重复重连的日志间隔（328ms）是否能排除"两条 ASR error 事件 + 两次 _on_asr_error 调用"——若是，则 lock 修复直接命中根因。
- [ ] D-C（关于周期性断连根因排查）：从日志看，1006 出现于 `keepalive ping timeout`（line 1809），1012 是 Gateway service restart（line 2633）。
  - **结论 D-C-1**：1012 是 Gateway 侧主动重启（OpenClaw Gateway 配置变更或自动重启），LinChat 侧无法消除，只能容忍。
  - **结论 D-C-2**：1006 keepalive 超时来源是 `ping_interval=30, ping_timeout=60`（asr_stream_client.py:19），合计 90s 没收到 pong。如果 Gateway 侧 nginx 默认 `proxy_read_timeout=60s` 会先于 ping_timeout 切断。
  - **本 batch 不动 1006/1012 根因**——这是 Gateway / Nginx 层问题，需要单独 batch 排查。仅在本 batch 修复"重连竞态"，使周期性断连后能稳定单线程重连。
  - 在 plan 第 7 节"需要安琳确认"中标注：是否需要新增 batch 排查 Gateway 1006/1012。

#### 改动 4.3：精简潜力评估

- consumer_session.py 269 行未超 300 行硬限制
- 未发现未使用 import（已用 ruff 类规则人工审视）
- 第 77-79 行有 DEPRECATED 注释代码块（diarize），属于 batch-08 voice mixin 重构范围，**不在本 batch 处理**

预估行数合计：+25 -8

## 4. 调查步骤（fix 类 batch 必填）

执行前依序完成：

- [ ] T1：确认数据库残留（test 类）
  ```bash
  source /home/dantsinghua/work/linchat/linchat/bin/activate
  cd /home/dantsinghua/work/linchat-batch-02/backend
  python -c "import django; django.setup()" 2>/dev/null
  python manage.py shell -c "from apps.media.models import MediaAttachment; print('media count:', MediaAttachment.objects.count())"
  python manage.py shell -c "from apps.memory.models import UserMemory, UserMemoryEmbedding; print('mem:', UserMemory.objects.count(), 'emb:', UserMemoryEmbedding.objects.count())"
  python manage.py shell -c "from apps.chat.models import Message; print('msg by user:', dict(Message.objects.values_list('user_id').annotate(__import__('django.db.models', fromlist=['Count']).Count('id'))))"
  ```
- [ ] T2：先复现失败测试（捕获完整 traceback 用于 commit message）
  ```bash
  pytest backend/tests/chat/test_media_cleanup_task.py -v 2>&1 | tail -50
  pytest backend/tests/memory/test_models.py -v 2>&1 | tail -30
  pytest backend/tests/memory/test_tasks.py::TestGenerateDailySummary -v 2>&1 | tail -40
  pytest backend/tests/memory/test_tasks.py::TestGenerateMonthlySummary -v 2>&1 | tail -40
  ```
- [ ] T3（ASR 类）：再次 grep 确认 `_reconnect_asr` 调用源
  ```bash
  rg "_reconnect_asr" backend/apps/voice/ -n
  ```
- [ ] T4（ASR 类）：执行前后用 `grep "ASR reconnect" /tmp/linchat-backend.log | head -20` 收集基线对比

## 5. 验证计划

### 5.1 自动化验证（每次改动后立即跑）

- [ ] `cd backend && pytest tests/chat/test_media_cleanup_task.py -v`（10 个测试全 PASS）
- [ ] `pytest tests/memory/test_models.py -v`（10 个测试全 PASS）
- [ ] `pytest tests/memory/test_tasks.py -v`（17 个测试全 PASS）
- [ ] `pytest tests/voice/ -v`（不应回归任何 voice 测试）
- [ ] `ruff check backend/tests/chat/test_media_cleanup_task.py backend/tests/memory/ backend/apps/voice/consumer_session.py`
- [ ] `mypy backend/apps/voice/consumer_session.py`（如有 mypy 配置）

### 5.2 全量回归

- [ ] `pytest backend/tests/ -v 2>&1 | tail -30` — 应从 1573 passed/13 failed 改善为 1582 passed/4 failed（剩下的 4 个是 batch-03 范畴）

### 5.3 手动验证（仅 ASR 修复，需要真实 Gateway）

- [ ] 启动应用：`./scripts/services.sh restart`
- [ ] 启动浏览器 voice ambient 模式，**让其连续运行 ≥ 10 分钟**（覆盖至少一次 6-8 分钟周期性断连）
- [ ] tail 日志监控：`tail -f /tmp/linchat-backend.log | grep -E "ASR reconnect|ASR WS closed|ASR error"`
- [ ] **预期**：每次断连只看到 **1 条** "ASR reconnected: user=X, attempt=Y" 日志，不应再出现同秒 2/3 条
- [ ] **预期**：若中途看到 "ASR reconnect already in progress, skip duplicate trigger"，说明锁生效了

### 5.4 回归验证

- [ ] 跑完整 voice 测试套件：`pytest backend/tests/voice/ -v`
- [ ] 跑 chat + memory：`pytest backend/tests/chat/ backend/tests/memory/ -v`

## 6. 回滚策略

由于本 batch 包含两个性质不同的修复（CI 测试 vs 生产 ASR），强烈建议 **2 个独立 commit**：

- Commit A：`fix(tests): 修复 9 个测试数据库隔离失败（batch-02a）`
  - 改动文件：test_media_cleanup_task.py, test_models.py, test_tasks.py
  - 回滚：`git revert <commit-A-hash>`
- Commit B：`fix(voice): ASR 重连竞态修复 — 旧会话完全关闭后再建新连接（batch-02b）`
  - 改动文件：consumer_session.py
  - 回滚：`git revert <commit-B-hash>`

如安琳要求合并为单 commit，回滚则 `git revert <commit>`，影响范围扩大但仍可控。

## 7. ⚠️ 需要安琳确认的事项 — 安琳决策记录（2026-04-17）

- [x] **C1（commit 拆分）— ACK 拆分**
  - 决定：拆 2 commit
    - `batch-02a`：测试修复（test_media_cleanup_task.py + test_models.py + test_tasks.py）
    - `batch-02b`：ASR 重连竞态修复（consumer_session.py）
  - 执行时 batch-executor 必须按此顺序提交，每 commit 单独通过自动化验证后才能进入下一个
- [x] **C2（ASR 周期性断连根因）— 新建 batch-02c**
  - 决定：不塞进 batch-02b，**新建 batch-02c** 专门排查 Gateway/Nginx 侧 1006/1012
  - 理由：batch-02b 是 linchat 代码改动（consumer_session.py），batch-02c 是 Gateway/Nginx 诊断（可能涉及 `proxy_read_timeout`、Gateway 自动重启策略、ping interval），性质完全不同
  - 执行时机：batch-02a/02b 完成并验证稳定后，作为独立 P1 batch 排期
  - batch-02c 占位已登记到 04-refactor-plan.json
- [x] **C3（Message 全表清理风险）— ACK 接受清空**
  - 决定：接受**首选方案** `Message.objects.all().delete()`
  - 已知影响：每次跑 test_tasks.py 会清空本机 user_id=7 的真实 Message 历史
  - batch-executor 执行前建议提醒但不强制备份；开发者需自觉（本机开发数据库，可接受）
  - 不采用 mock 方案（会绑死 task_helpers 实现路径）
  - 不采用独立 test DB 方案（扩大 scope，超出本 batch 范围）
- [ ] **C4（test_tasks.py 行数）**：不拆，保持 508 → 516 行
- [ ] **C5（test_models.py cleanup FK 顺序）**：按 FK 顺序先 Embedding 后 Memory，符合团队偏好
- [ ] **C6（ASR lock 懒初始化）**：保持 SessionMixin 方法内懒初始化，不强制在主 Consumer `__init__` 预创建

**所有阻塞项已解除，可直接进入 executor 阶段。**

## 8. 执行预算

- 预计 Claude Code 需要的 tool calls：
  - 测试修复：~12 calls（3 文件 × 2 read/edit + 4 测试运行）
  - ASR 修复：~8 calls（1 文件 read/edit + 2 grep + 4 验证 + 日志对比）
  - 合计：~20 tool calls
- 预计 token 消耗：~50k input / ~12k output
- 预计完成时间：1 session（30-45 分钟）

预算在 04-refactor-plan.json `estimated_sessions=1` 范围内。

## 9. 修复后效果对比

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| pytest 失败数 | 13 failed | 4 failed（剩 batch-03 的 test_document_agent 等 4 个） |
| ASR reconnect 同秒重复 | 2-3 次/事件 | 1 次/事件 |
| ASR reconnect 失败重复日志 | 2-3 条/失败 | 1 条 + N 条 attempt warning |
| 周期性断连本身（Gateway 侧） | 6-8 分钟 | 不变（C2 待确认是否新增 batch） |
