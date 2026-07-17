# Batch batch-26 执行计划

> 生成时间：2026-07-17
> 类型：test | 优先级：P3 | 风险：low
> 预估：4 文件（实际仅新增 2 个测试文件）/ ~350 行 / 1 session
> 依赖：depends_on=[]（无前置依赖，可直接执行）
> SLO 影响：无（blocks_slo=null）
> pre_approved_by_user：true

## 1. 任务理解（一句话）

纯新增测试批次：为 `apps/users/views.py`（实测 105 stmts / 50%）和
`apps/voice/services/voice_persist_service.py`（实测 108 stmts / 66%）补齐未覆盖分支，
把两个模块覆盖率抬到 **80%+**，业务代码零改动。users/views 涉及登录/验证码/SM3，
一律 mock 到 service 层，不碰真实加密逻辑。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/users/views.py | 222 | 0（只读参考） | 无 | 低 | 低（<300 行，无未用 import） |
| 2 | backend/apps/voice/services/voice_persist_service.py | 143 | 0（只读参考） | 无 | 低 | 低（<300 行，无未用 import） |
| 3 | backend/tests/users/test_views.py（新建） | 0 | +180 ~200 | 新增测试 | 低 | — |
| 4 | backend/tests/voice/test_voice_persist_service.py（新建） | 0 | +140 ~160 | 新增测试 | 低 | — |

> 注：两个业务文件均在 300 行硬限制内，纯只读参考，不触碰。
> `plan.json` 把两个测试文件同时列入 `files_touched` 与 `new_files`——确认为**新建**：
> `tests/users/` 现无 `test_views.py`（39KB 的是 `test_member_views.py`，grep 误匹配）；
> `tests/voice/` 现无 `test_voice_persist_service.py`。

## 3. 详细改动计划

### 文件 3：backend/tests/users/test_views.py（新建）

**Mock 风格对齐既有 `tests/users/test_member_views.py`**：
`RequestFactory` 造 request → `View.as_view()` → `async_to_sync(view)(request)` 驱动 async 视图 →
手动挂 `request.user_id / username / member_type / user_type` 属性 →
`patch("apps.users.views.AuthService" / ".CaptchaService" / ".MemberService")` 到 service 层
（AsyncMock）。**不碰 SM3/SM4/真实验证码**，全部 mock service 返回值。多数用例 `@pytest.mark.django_db` 非必需
（service 已 mock），仅 MeView/helpers 纯函数无 DB。

#### 缺口分组（missing = 28-29, 34-37, 47-52, 62-102, 113-123, 133, 167, 178-179, 207-219，共 52 stmts）

##### 分组 A — `CaptchaView.get`（line 47-52）
- A1 `test_captcha_success`：patch `CaptchaService.generate`=AsyncMock(return {...})，
  断言 200 + body.data（覆盖 48-49）。
- A2 `test_captcha_service_error`：`generate` side_effect=Exception → 断言 500 +
  message="验证码生成失败"（覆盖 50-54）。

##### 分组 B — `LoginView.post`（line 62-102，本批最大缺口）
- B1 `test_login_invalid_json`：`request.body=b"not-json"` → 400 code=INVALID_REQUEST（覆盖 63-68）。
- B2 `test_login_validation_error`：body 缺 captcha_code → serializer 无效 → 400
  code=VALIDATION_ERROR，取 first_error（覆盖 70-76）。
- B3 `test_login_success`：patch `AuthService.login`=AsyncMock(return dict：user_id/username/
  expire_time(datetime)/token)；patch `apps.users.views.set_token_cookie`（避免真实 cookie）→
  断言 200 + data.user_id + set_token_cookie 被调用（覆盖 78-96，含 `_get_client_ip` REMOTE_ADDR 分支）。
- B4 `test_login_with_xff_header`：request.META 设 `HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8"` →
  断言 login 收到 client_ip=="1.2.3.4"（覆盖 helper line 28-29 真分支）。
- B5 `test_login_auth_exception`：`login` side_effect=一个带 remaining_seconds 的 AuthException
  子类（AccountLockedError）→ 断言 error_response 走 `_handle_auth_exception`，body 含
  remaining_seconds（覆盖 98-99 + helper 34-39 的 extra 真分支）。
- B6 `test_login_generic_exception`：`login` side_effect=RuntimeError → 500 code=LOGIN_ERROR（覆盖 100-105）。

##### 分组 C — `LogoutView.post`（line 113-123）
- C1 `test_logout_success`：request 挂 `user_id / token_hash`，patch `AuthService.logout`=AsyncMock →
  断言 200 + logout awaited + clear_token_cookie 调用（覆盖 113-117, 121-123）。
- C2 `test_logout_no_session`：request 无 user_id/token_hash → logout **未** awaited，仍返回 200
  （覆盖 116 False 分支）。
- C3 `test_logout_swallows_error`：`logout` side_effect=Exception → 不抛，仍返回 200（覆盖 118-119）。

##### 分组 D — `MeView.get` 未登录分支（line 133）
- D1 `test_me_unauthorized`：request 无 user_id → 401 code=UNAUTHORIZED（覆盖 132-136）。
- D2（巩固）`test_me_authorized`：挂 user_id/username → 200 + data.user_id（若已被 middleware 测试覆盖则仅巩固）。

##### 分组 E — `MemberListCreateView` 剩余分支（line 167, 178-179, 207-219）
> GET 200/403、POST 成功、POST USERNAME_EXISTS 已由 `test_member_views.py` 覆盖，本批仅补剩余。
- E1 `test_member_post_forbidden`：POST，request.member_type="guest" → 403 code=FORBIDDEN（覆盖 165-170，line 167）。
- E2 `test_member_post_validation_error`：POST multipart 缺 audio/username 非法 → serializer 无效 →
  400 code=VALIDATION_ERROR（覆盖 177-182，line 178-179）。
- E3 `test_member_post_voiceprint_error`：patch `MemberService.create_member`=AsyncMock(
  side_effect=VoiceprintRegistrationError) → 400 code=VOICEPRINT_FAILED（覆盖 207-211）。
- E4 `test_member_post_value_error`：`create_member` side_effect=ValueError → 400 code=VALIDATION_ERROR（覆盖 212-216）。
- E5 `test_member_post_generic_error`：`create_member` side_effect=RuntimeError → 500（覆盖 217-222）。
  > E1-E5 造 POST 用 `factory.post(..., data={username,password,member_type}, format multipart)` +
  > `request.FILES` 挂 `SimpleUploadedFile`（复用 test_member_views 的 `_make_audio_file`）。

### 文件 4：backend/tests/voice/test_voice_persist_service.py（新建）

**Mock 风格对齐既有 `tests/voice/test_voice_pipeline.py` 的 `_VPS` 段**：
`patch("apps.voice.services.voice_persist_service.<dep>")` + AsyncMock；
DB 类 sync 方法用 `@pytest.mark.django_db` + 真实 ORM（比 mock 更省成本）。
`_VPS = "apps.voice.services.voice_persist_service"`。

#### 缺口分组（missing = 59-74, 80-95, 128-140，共 37 stmts）

##### 分组 F — `persist_audio_attachment` 主流程（line 59-74）
- F1 `test_persist_happy_path`：patch `_VPS.voice_session_service`（get_audio_chunks→[b"\x00"*640]
  AsyncMock、clear_audio_chunks AsyncMock）、`_VPS.voice_persist_service.upload_to_minio`=AsyncMock、
  `..._atomic_mark_voice`=AsyncMock → 断言 upload/_atomic_mark/clear 各 awaited once，
  storage_path 形如 `media/<uid>/<date>/<uuid>.wav`（覆盖 59-72）。
- F2 `test_persist_empty_chunks_returns_early`：get_audio_chunks→[] → 断言 upload **未** 调用
  （覆盖 57-58 早退，巩固）。
- F3 `test_persist_atomic_failure_compensates`：`_atomic_mark_voice` side_effect=Exception、
  patch `..._VPS.voice_persist_service.delete_from_minio`=AsyncMock → 断言 delete_from_minio awaited
  （MinIO 补偿删除）且不向外抛（外层 73-74 except 兜底，覆盖 65-70 + 73-74）。
- F4 `test_persist_uses_cache_user_id`：传 cache_user_id=9、user_id=1 → 断言 get_audio_chunks/
  clear_audio_chunks 收到 cache_uid=9，但 storage_path 用 user_id=1（覆盖 54 的 cache_uid 分支）。

##### 分组 G — `_atomic_mark_voice` DB 落库（line 80-95）
- G1 `test_atomic_mark_creates_attachment`（django_db）：预置 user Message（request_id=R, role="user"）
  + assistant Message（request_id=R, role="assistant"）→ 调用 `_atomic_mark_voice(...)` →
  断言两条 Message.is_voice=True + MediaAttachment 新建 1 条（media_type=AUDIO、storage_path、
  duration_seconds、expires_at）（覆盖 81-95，含 user_msg + asst_msg 两个真分支）。
- G2 `test_atomic_mark_no_matching_message`（django_db）：request_id 不存在任何 Message →
  断言无 MediaAttachment 创建、不抛异常（覆盖 82-83 / 92-93 False 分支）。

##### 分组 H — `_count_and_delete_excess` 清理上限（line 128-140）
- H1 `test_count_delete_excess_over_limit`（django_db + override_settings
  VOICE_AMBIENT_RECORD_ONLY_LIMIT=2）：造 5 条 record-only user 语音消息（is_voice=True，
  无对应 assistant 回复）→ 调用 → 断言返回 3、最旧 3 条按 created_time 被删、剩 2 条（覆盖 129-140）。
- H2 `test_count_delete_below_limit_noop`（django_db，limit=10）：造 3 条 → 返回 0、无删除（覆盖 134-135 早退）。
- H3 `test_count_delete_excludes_replied`（django_db，limit=1）：造 2 条 user 语音，其中 1 条有
  assistant is_voice 回复（同 request_id）→ 应只统计未回复的 record-only（Subquery 排除），
  count=1<=limit → 返回 0（覆盖 129 的 exclude Subquery 逻辑）。

## 4. 调查步骤（fix 类专用）

不适用（本批为 test 类型）。当前覆盖率已实测，见 5.3。

## 5. 验证计划

### 5.1 自动化验证（executor 阶段照 systemd-run 沙箱模板执行）
- [ ] `pytest tests/users/test_views.py -v`
- [ ] `pytest tests/voice/test_voice_persist_service.py -v`
- [ ] `ruff check tests/users/test_views.py tests/voice/test_voice_persist_service.py`
- [ ] 覆盖率见 5.3

> 沙箱执行姿势（本 initializer 已用同款实测通过）：
> `systemd-run --user --scope --quiet -p MemoryMax=3G bash -c 'cd backend && source ../linchat/bin/activate && python -m pytest ...'`

### 5.2 手动验证步骤
无（纯单测，validation.manual 为空）。

### 5.3 覆盖率验证（核心指标）
- 当前基线（实测 main HEAD，2026-07-17，systemd-run 包裹）：
  ```
  apps/users/views.py                            105     52    50%   28-29,34-37,47-52,62-102,113-123,133,167,178-179,207-219
  apps/voice/services/voice_persist_service.py   108     37    66%   59-74,80-95,128-140
  ```
- 目标：两个模块均 > 80%。分组 A-E 后 users/views 预计 miss ≤5（≥95%）；
  分组 F-H 后 voice_persist 预计 miss ≤3（≥97%）。

### 5.4 回归验证
- [ ] `pytest tests/users/ -q`（当前基线 84 passed，不得下降）
- [ ] `pytest tests/voice/ -q`（当前基线 761 passed，不得下降）
- [ ] 跨 app 无需（纯新增测试，业务代码零改动）

## 6. 回滚策略

纯测试新增，安全整体 revert：
```bash
git revert <commit-hash>                                   # 单 commit 回滚
# 或直接删除两个新测试文件
git rm backend/tests/users/test_views.py backend/tests/voice/test_voice_persist_service.py
```
无业务代码/schema/迁移改动，回滚零副作用。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **`plan.json` 把两个业务文件列入 `files_touched`** — 本计划将其视为**只读参考**，
      业务代码零改动（对齐任务指令）。确认无异议。
- [ ] **文件行数与 plan.json 略有出入**：views.py 222 行=105 stmts、voice_persist 143 行=108 stmts，
      均 <300 硬限制，无需拆分，仅记录事实。
- [ ] **豁免项（极小）**：views.py 的 `logger.info/exception` 纯日志行、voice_persist 的
      `delete_from_minio` 内部 `except`（45-46，已覆盖）等不追求 100%；若个别 `logger.exception`
      分支断言不稳定，最坏豁免 1-2 行日志语句，仍稳达 80%+。**无高 mock 成本分支需整体豁免**
      （G/H 的 DB sync 方法用真实 ORM+django_db 即可，比 mock 更省）。

✅ 无阻塞事项，可直接进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：~18（读参考 0（已读）+ 写 2 个测试文件 + 迭代跑测/覆盖率 12~16）
- 预计 token：中（2 个 ~180 行测试文件；业务文件已在 initializer 阶段读完）
- 预计时间：1 session（estimated_sessions=1，不超预算）
