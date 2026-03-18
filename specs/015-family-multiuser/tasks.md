# Tasks: 家庭多用户系统

**Input**: Design documents from `/specs/015-family-multiuser/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/members-api.md, quickstart.md

**Tests**: 各 Phase 包含对应测试任务，遵循宪法第三条（服务层 ≥ 95%，总体 ≥ 80%）。

**Organization**: Tasks grouped by user story (US1-US5)，按 spec.md 优先级排序。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1-US5)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: SysUser 模型扩展与数据库迁移

- [X] T001 扩展 SysUser 模型：添加 member_type/guest_expires_at 字段和 is_member()/is_guest_expired() 方法（不添加 is_deleted 字段，系统不支持删除）in `backend/apps/users/models.py`
- [X] T002 生成数据库迁移文件 0005_add_multiuser_fields in `backend/apps/users/migrations/`
- [X] T003 执行数据库迁移 `python manage.py migrate`，验证字段生效

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 核心基础设施 — 认证中间件扩展、登录流程变更、前端状态管理基础

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 扩展 TokenAuthMiddleware，两个独立检查点：**检查点 A（认证用户状态）**：设置 request.user_id 后，立即校验该登录用户 guest_expires_at 是否已过期 → 返回 401 并记录 WARNING 日志"过期访客 {user_id} 尝试使用存量 Token 访问"（使过期访客的存量 Token 立即失效）；**检查点 B（目标用户解析）**：解析 X-Target-User-Id Header，验证目标用户有效性（存在、status=1、未过期），任一条件不满足则返回 400 错误（错误码 TARGET_USER_INVALID，message 包含具体原因如"目标用户不存在"/"目标用户已过期"/"目标用户已禁用"）并记录 WARNING 日志（宪法 1.4 显式失败原则，禁止静默 fallback）；target_user_id 切换成功时记录 INFO 日志"用户 {user_id} 切换目标到 {target_user_id}"；设置 `request.target_user_id`（`request.user_id` 始终为登录用户不覆盖）；当 token_info 缺少 member_type 时从数据库查询 SysUser.member_type 并回填 Redis（兼容存量 Token）。注：status=1 校验合理——新用户创建为原子操作直接 status=1 入库，status=0 仅用于过期访客，不存在需要切换到 status=0 用户的场景 in `backend/apps/common/middleware.py`
- [X] T005 扩展 AuthService.login()：密码验证后检查 guest_expires_at（返回错误码 ACCOUNT_EXPIRED，提示"账号已过期"，**并记录 WARNING 日志"过期访客 {username} 尝试登录被拒"**），Token Redis 数据中加入 member_type 字段。无需 is_deleted 检查（系统不支持删除）in `backend/apps/users/services.py`
- [X] T006 扩展 MeView：返回 member_type 字段 in `backend/apps/users/views.py`
- [X] T007 [P] 扩展 UserRepository：添加 list_members(include_expired=False)（始终过滤 status=1，含 is_expired 标记；默认仅返回有效且未过期的用户，status=0 用户对系统完全不可见）方法 in `backend/apps/users/repositories.py`
- [X] T008 [P] 扩展前端 User 类型定义：添加 member_type 字段 in `frontend/src/types/index.ts`
- [X] T009 [P] 创建 memberStore 骨架：targetUserId/targetUsername/members/isViewingOther 状态定义 + loadMembers action + action 签名（setTargetUser/clearTarget 留空实现，Phase 4 T035 填充） in `frontend/src/stores/memberStore.ts`
- [X] T010 [P] 创建 memberService：getMembers(显式传 include_expired=true 以满足模态框展示过期访客灰色状态需求，注意 API 默认 false)/createMember(FormData: username+password+member_type+audio) API 调用（不提供 deleteMember，系统不支持删除）in `frontend/src/services/memberService.ts`
- [X] T011 扩展 Axios 拦截器（请求+响应）：**请求拦截器**：当 memberStore.isViewingOther 为 true 时自动添加 X-Target-User-Id Header（所有请求均携带，但仅聊天 API 使用此值，其他 API 忽略）；**响应拦截器**：捕获 400 响应中 code=TARGET_USER_INVALID 错误，自动调用 memberStore.clearTarget() 清除 targetUserId 并恢复自身视角，同时显示 toast 提示具体失败原因（从 response.data.message 取值，如"目标用户已过期"），确保宪法 1.4 显式失败原则的前端侧闭环 in `frontend/src/services/api.ts`
- [X] T012 扩展前端登录页错误处理：解析后端登录拒绝响应中的错误码（ACCOUNT_EXPIRED → "账号已过期，请联系家庭成员"），在登录表单下方展示对应中文提示。无需处理 ACCOUNT_DELETED（系统不支持删除）in `frontend/src/app/login/page.tsx`
- [X] T013 [P] 适配 chat API 使用 `request.target_user_id`：chat/views.py 中 `request.user_id` → `request.target_user_id`（消息查询、创建、AI 回复 pipeline）；同步检查 agent pipeline 内部的 user_id 传播链（agent_service.py 调用 LangGraph 时传入的 user_id、记忆召回中的 user_id 查询），确保全链路使用 target_user_id in `backend/apps/chat/views.py` + `backend/apps/chat/services/`
- [X] T014 [P] 适配 media API 使用 `request.target_user_id`：get_media 增加 `target_user_id` 附件权限校验（允许查看目标用户的消息附件）in `backend/apps/media/views.py`
- [X] T015 [P] 确认 voice API 无需修改：声纹注册在用户创建时由 MemberService 内部调用 Gateway API 完成，不通过 voice/views.py 端点；SpeakerListCreateView.post 继续使用 `request.user_id`（仅用于用户自行重新注册声纹的场景，非本特性核心路径）in `backend/apps/voice/views.py`

**Checkpoint**: Foundation ready — 认证扩展完成，聊天/媒体/语音 API 适配完成，前端状态管理就绪，user story 实现可开始

### Tests for Phase 2

- [X] T016 [P] 测试 TokenAuthMiddleware 扩展：验证 `request.user_id` 始终为登录用户不变、`request.target_user_id` 正确设置、X-Target-User-Id 解析、member 可切换/guest 不可切换、目标用户不存在或已过期时返回 400 错误（TARGET_USER_INVALID）、Token 有效但 guest_expires_at 已过期的已登录访客请求返回 401（模拟访客登录后在会话期间过期的场景）in `backend/tests/users/test_middleware.py`
- [X] T017 [P] 测试 AuthService.login() + MeView 扩展：验证过期 guest 拒绝登录（错误码 ACCOUNT_EXPIRED）、正常用户 Token 含 member_type；验证 GET /api/v1/auth/me 返回 member_type 字段（member 返回 "member"、guest 返回 "guest"）in `backend/tests/users/test_auth_service.py`
- [X] T018 [P] 测试 UserRepository 扩展 + SysUser 模型方法：list_members include_expired 参数控制过期访客返回、status=0 用户始终不返回；SysUser.is_member() 和 is_guest_expired() 方法正确性 in `backend/tests/users/test_user_repository.py`
- [X] T019 [P] 测试聊天/媒体 API target_user_id 适配：GET /api/v1/chat 携带 X-Target-User-Id 时返回目标用户消息而非登录用户消息、GET /api/v1/media 返回目标用户附件 in `backend/tests/users/test_target_user_views.py`
- [X] T020 [P] 测试前端登录页错误处理：ACCOUNT_EXPIRED 错误码展示"账号已过期，请联系家庭成员"提示、正常登录无额外提示 in `frontend/src/app/login/__tests__/page.test.tsx`

---

## Phase 3: User Story 1 — 成员查看和管理家庭 (Priority: P1) 🎯 MVP

**Goal**: 成员登录后可在模态框中查看家庭成员列表、创建新用户（步骤1: 类型+凭据）；访客无管理入口。系统不提供删除功能

**Independent Test**: 成员登录 → 点击头像按钮 → 模态框显示用户列表 → 创建新用户（步骤1）→ 列表实时更新；访客登录 → 无头像按钮

### Implementation for User Story 1

- [X] T021 [P] [US1] 创建 MemberService：list_members(include_expired=False)（含 is_expired 标记，始终过滤 status=1）、create_member(username, password_encrypted, member_type, audio_file, created_by_user_id)（原子操作：校验用户名唯一 → 调用 Gateway 声纹注册 API → Gateway 失败则抛 VoiceprintRegistrationError 返回，数据库无写入 → Gateway 成功后 @transaction.atomic 内创建 SysUser(status=1) + SpeakerProfile(gateway_speaker_id)）；SM4解密密码→SM3哈希、设置 guest_expires_at；create_member 操作 MUST 记录 INFO 级别审计日志（操作人 user_id、目标用户 username/user_id、操作类型 create，符合宪法 6.2 业务关键操作日志要求）；同时在 `backend/apps/users/exceptions.py` 中创建 UsernameExistsError 和 VoiceprintRegistrationError 异常类（继承 BusinessException），MemberService 抛出对应异常。不提供 delete_member 方法（系统不支持删除）in `backend/apps/users/services.py`
- [X] T022 [P] [US1] 创建 CreateMemberSerializer（username/password/member_type/audio 校验，username 唯一性由数据库级 UNIQUE 约束保证、大小写敏感，audio 为 FileField 必填，无 guest_expires_days 参数，固定 7 天由后端自动计算）和 MemberListSerializer（含 is_expired）in `backend/apps/users/serializers.py`
- [X] T023 [US1] 创建 MemberListCreateView（GET 列表 + POST 创建，POST 接受 multipart/form-data 含 audio 文件），member_type=member 权限校验。不创建 MemberDeleteView（系统不支持删除）in `backend/apps/users/views.py`
- [X] T024 [US1] 注册 members/ URL 路由：GET+POST /api/v1/members/（不注册 DELETE）in `backend/apps/users/urls.py`
- [X] T025 [P] [US1] 创建 MemberSwitchModal 组件：用户列表（头像首字母+背景色按 user_id % 8 从 8 色方案选取（见 plan R-007）+用户名+类型标签，过期访客灰色展示在底部不可点击；status=0 用户不在列表中出现）、"添加用户"入口、点击用户的 onSelect 回调（切换逻辑由 Phase 4 T034 实现）。不提供删除按钮 in `frontend/src/components/members/MemberSwitchModal.tsx`
- [X] T026 [P] [US1] 创建 CreateMemberWizard 组件 Step 1：成员/访客类型选择 + 用户名密码输入（仅前端状态，不调用 API）；Step 1 完成后进入 Step 2（声纹录音，Phase 6 T047 实现），全部完成后一次性提交 `POST /api/v1/members/`（multipart/form-data: username + SM4加密password + member_type + audio）；提交失败时展示后端返回的 message 文本（USERNAME_EXISTS / VOICEPRINT_FAILED / VALIDATION_ERROR） in `frontend/src/components/members/CreateMemberWizard.tsx`
- [X] T027 [US1] 修改 MessageInput：左侧添加圆形头像按钮（首字母+颜色，仅 member_type=member 显示），点击打开 MemberSwitchModal in `frontend/src/components/chat/MessageInput.tsx`
- [X] T028 [US1] 修改 ChatPage：集成 MemberSwitchModal 状态管理，从 useAuth 读取 member_type 控制头像按钮可见性 in `frontend/src/app/chat/page.tsx`
- [X] T029 [US1] 扩展 useAuth hook：解析 /auth/me 返回的 member_type 并存入状态；**auth 成功且 member_type=member 时按顺序执行初始化链：loadMembers() → restoreTargetFromStorage()（确保 members 列表就绪后再校验和恢复 localStorage 中的 targetUserId）**；**logout 流程中 MUST 调用 memberStore.clearTarget() 清除 localStorage 中的 targetUserId**（防止跨登录会话的数据泄露）in `frontend/src/hooks/useAuth.tsx`

**Checkpoint**: 成员管理全链路可用，用户列表+创建向导 Step1（凭据收集）功能完整。创建向导 Step2（声纹录音）+ 一次性提交在 Phase 6 (US4) 完成后端到端验收

### Tests for Phase 3

- [X] T030 测试 MemberService：list_members 含 is_expired 标记/include_expired 参数控制、create_member 原子操作（mock Gateway 声纹 API 成功→验证 SysUser(status=1)+SpeakerProfile 同时创建 / mock Gateway 失败→验证数据库无残留）/ SM4→SM3 密码处理 / guest_expires_at 自动设置 / UsernameExistsError 抛出 / VoiceprintRegistrationError 抛出 in `backend/tests/users/test_member_service.py`
- [X] T031 [P] 测试 Members API 视图：GET 列表权限（member 200/guest 403）、GET include_expired 参数、POST 创建 multipart/form-data（参数校验/用户名重复 400/mock Gateway 声纹失败 400 VOICEPRINT_FAILED/成功 201 返回 status=1）in `backend/tests/users/test_member_views.py`
- [X] T032 [P] 测试 MemberSwitchModal：member 登录显示用户列表、guest 登录不渲染、点击用户触发切换回调、过期访客灰色展示在列表底部、无删除按钮 in `frontend/src/components/members/__tests__/MemberSwitchModal.test.tsx`
- [X] T033 [P] 测试 useAuth hook 扩展：/auth/me 返回 member_type 字段正确解析到状态；**auth 成功后按顺序执行 loadMembers → restoreTargetFromStorage 初始化链**；**logout 调用后 memberStore.targetUserId 为 null 且 localStorage 中 linchat_target_user_id 已清除** in `frontend/src/hooks/__tests__/useAuth.test.tsx`

---

## Phase 4: User Story 2 — 成员快速切换到其他用户视角 (Priority: P1)

**Goal**: 成员在模态框用户列表中点击目标用户，页面刷新为目标用户的聊天历史和记忆；切换不改变登录身份

**Independent Test**: 成员点击用户 A → 聊天历史变为 A 的消息 → 再次点击切换按钮，管理功能仍可用 → 点击自己恢复原视角

### Implementation for User Story 2

- [X] T034 [US2] 在 MemberSwitchModal 中实现切换逻辑：点击用户 → 先调用 chatStore.abortStream()（中断活跃 SSE 流，复用现有停止按钮逻辑）→ memberStore.setTargetUser(userId, username) → chatStore.clearMessages() + loadHistory()（loadHistory 自动携带 X-Target-User-Id 读取目标用户聊天数据）→ 关闭模态框；过期访客禁止点击（列表中灰色展示、点击无响应）；切换到自己时忽略操作 in `frontend/src/components/members/MemberSwitchModal.tsx`
- [X] T035 [US2] 在 memberStore 中实现完整切换链：setTargetUser 同步写 localStorage、clearTarget 清除 localStorage；**初始化时序**：不在 store 创建时立即恢复 localStorage，而是提供 restoreTargetFromStorage() 方法，由 T029 useAuth 在 auth 成功 + loadMembers() 完成后显式调用（确保 members 列表就绪后再校验 localStorage 中的 targetUserId 是否有效，无效则清除），校验通过后触发 chatStore.loadHistory() 加载目标用户聊天记录。注：仅新标签页初始化时读取 localStorage，不实现跨标签页实时同步（符合宪法 9.2 单并发用户约束——同一时刻仅一人操作一个标签页） in `frontend/src/stores/memberStore.ts`
- [X] T036 [US2] 修改 ChatPage 顶部导航：切换聊天发言人后显示"正在查看 [用户名] 的对话"提示条，提供"回到自己"按钮 in `frontend/src/app/chat/page.tsx`
- [X] T037 [US2] 修改 MessageInput 头像按钮：切换视角后头像更新为目标用户的首字母和颜色 in `frontend/src/components/chat/MessageInput.tsx`

**Checkpoint**: 用户切换全链路可用，聊天历史正确切换，管理权限基于登录身份不受影响

### Tests for Phase 4

- [X] T038 [P] 测试 memberStore：setTargetUser 写入 localStorage + isViewingOther 计算、clearTarget 清除 localStorage、restoreTargetFromStorage() 在 members 列表就绪后校验 localStorage（有效 ID 恢复成功 / 无效 ID 自动清除） in `frontend/src/stores/__tests__/memberStore.test.ts`
- [X] T039 [P] 测试切换逻辑前端：点击用户先触发 chatStore.abortStream() 中断活跃 SSE 流 → 再触发 setTargetUser + chatStore.clearMessages + loadHistory 调用链、过期访客点击无响应（灰色禁用态）、切换到自己时忽略操作、提示条"正在查看 [用户名] 的对话"显示与"回到自己"按钮、**SSE 中断场景**：mock 活跃 SSE 流时切换用户，验证流被中断后再加载目标用户数据 in `frontend/src/components/members/__tests__/MemberSwitchModal.test.tsx`
- [X] T040 [P] 测试 Axios 拦截器：**请求**：isViewingOther=true 时请求自动携带 X-Target-User-Id Header、target=self 时不携带、clearTarget 后请求不再携带；**响应**：mock 400 TARGET_USER_INVALID 响应后 memberStore.targetUserId 自动清除、toast 显示错误 message、后续请求不再携带 Header in `frontend/src/services/__tests__/api.test.ts`

---

## Phase 5: User Story 3 — 访客临时访问 (Priority: P2)

**Goal**: 成员创建的访客账号有有效期（默认 7 天），过期后自动失效禁止登录；访客仅可使用聊天功能

**Independent Test**: 创建访客 → 访客登录使用聊天 → 验证无管理入口 → 等待过期（或手动修改 DB）→ 访客无法登录

### Implementation for User Story 3

- [X] T041 [P] [US3] 创建 expire_guests Celery 定时任务：扫描过期访客并设 status=0 in `backend/apps/users/tasks.py`
- [X] T042 [US3] 在 Celery beat_schedule 中注册 expire-guests 任务（每小时执行）in `backend/core/celery.py`
- [X] T043 [US3] 前端访客登录体验：当 useAuth 返回 member_type=guest 时，ChatPage 顶部导航显示"访客"角色标签；验证 MessageInput 头像按钮（T027 已实现 member-only 条件渲染）和 MemberSwitchModal 对 guest 不可见 in `frontend/src/app/chat/page.tsx`

**Checkpoint**: 访客全生命周期可用 — 创建→登录→聊天→过期→拒绝登录

### Tests for Phase 5

- [X] T044 测试 expire_guests 任务：过期 guest 被设 status=0、未过期 guest 不受影响、member 不受影响 in `backend/tests/users/test_guest_expiry.py`
- [X] T045 [P] 测试访客登录体验：guest 登录后 ChatPage 顶部显示"访客"角色标签、MessageInput 无头像按钮、MemberSwitchModal 不渲染 in `frontend/src/app/chat/__tests__/page.test.tsx`

---

## Phase 6: User Story 4 — 声纹注册 (Priority: P2)

**Goal**: 用户创建流程步骤 2 — 声纹录音（10-30 秒）→ 录音完成后向导一次性提交全部数据（凭据+音频）→ 后端原子创建（声纹注册+用户入库）；不可跳过

**Independent Test**: 点击创建用户 → 完成步骤 1（凭据）→ 进入步骤 2 录音 → 10 秒达标后完成录音 → 提交创建 → 后端原子完成 → 用户出现在列表中

### Implementation for User Story 4

- [X] T046 [P] [US4] 创建 VoiceprintRecorder 组件：浏览器 MediaRecorder 录音、10-30 秒倒计时进度条、不足 10 秒禁用"完成录音"按钮、30 秒自动停止、浏览器 visibilitychange 事件暂停录音并提示用户重新录制；录音完成后通过 onRecordingComplete(audioBlob) 回调将音频 Blob 传递给父组件（不直接调用 API） in `frontend/src/components/members/VoiceprintRecorder.tsx`
- [X] T047 [US4] 在 CreateMemberWizard 中集成 Step 2：步骤 1 完成后进入声纹录音界面（VoiceprintRecorder），录音完成后向导将全部收集的数据（username、SM4 加密 password、member_type、audio Blob）一次性通过 memberService.createMember() 提交到 `POST /api/v1/members/`（FormData）；提交中显示 loading 状态（含"正在注册声纹..."提示）；成功后刷新成员列表并关闭向导；失败时显示具体错误（VOICEPRINT_FAILED → "声纹注册失败，请重新录制" / USERNAME_EXISTS → "用户名已存在"），允许重试、不可跳过 in `frontend/src/components/members/CreateMemberWizard.tsx`

**Checkpoint**: 用户创建完整流程端到端可用（向导 Step1 凭据 + Step2 录音 → 一次性提交 → 后端原子创建声纹+用户）

### Tests for Phase 6

- [X] T048 [P] 测试 VoiceprintRecorder 组件：录音不足 10 秒时"完成录音"按钮禁用、录音达 30 秒自动停止、录音完成后触发 onRecordingComplete 回调并传递 audioBlob、失败后可重新录制 in `frontend/src/components/members/__tests__/VoiceprintRecorder.test.tsx`
- [X] T049 [P] 测试 CreateMemberWizard 完整流程：Step1 填写凭据后进入 Step2 声纹界面、录音完成后一次性提交 POST /members/（mock API 成功→用户出现在列表+向导关闭 / mock API 返回 VOICEPRINT_FAILED→显示错误+停留 Step2 允许重试 / mock API 返回 USERNAME_EXISTS→显示错误+允许返回 Step1 修改）in `frontend/src/components/members/__tests__/CreateMemberWizard.test.tsx`

---

## Phase 7: User Story 5 — Web 语音模式统一为 ambient (Priority: P3)

**Goal**: 浏览器端语音连接统一使用 ambient 模式参数

**Independent Test**: 浏览器发起语音连接 → WebSocket 日志确认 mode=ambient → 语音交互正常

### Implementation for User Story 5

- [X] T050 [US5] 修改语音模式入口：点击语音按钮前先检查 memberStore.isViewingOther，若为 true 则自动调用 clearTarget() 恢复自身视角 + chatStore.loadHistory() 重新加载自身聊天历史，并显示 toast 提示"语音模式需使用本人声纹，已切换回自身视角"（语音模式必须使用真实登录用户的声纹）in `frontend/src/components/chat/MessageInput.tsx`
- [X] T051 [US5] 修改前端语音连接配置：session.configure 消息中 mode 硬编码为 "ambient" in `frontend/src/hooks/useVoiceMode.ts`
- [X] T052 [US5] 清理前端 voice_chat 模式相关代码：移除模式选择 UI（如有）、简化 VoiceModePanel 状态机中 voice_chat 特有逻辑 in `frontend/src/components/voice/VoiceModePanel.tsx`
- [X] T053 [US5] 检查并清理后端 voice_chat 模式分支：检查 backend/apps/voice/ 中 WebSocket consumer 是否存在 voice_chat 模式的差异化处理逻辑，如有则统一为 ambient 模式或移除无用分支。注：家庭场景无第三方客户端风险，后端不需要拒绝 voice_chat 模式连接（仅前端硬编码即可），但应清理无用代码路径 in `backend/apps/voice/consumers.py`

**Checkpoint**: Web 端语音模式统一为 ambient，前后端 voice_chat 模式代码清理完毕

### Tests for Phase 7

- [X] T054 [P] 测试语音连接 mode 配置：session.configure 消息中 mode 固定为 "ambient"、无 voice_chat 模式选择 UI 残留 in `frontend/src/hooks/__tests__/useVoiceMode.test.ts`

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 数据重置、文档更新、最终验收

- [X] T055 创建全量清库脚本 management command：清空 PostgreSQL 所有业务表（SysUser/Message/UserMemory/SpeakerProfile/MediaAttachment/LangGraphExecution 等）、MinIO 存储桶、向量库、Langfuse 监控数据；然后初始化管理员账户（anlin）：命令接受 `--password` 参数（明文密码，命令内部执行 SM3 哈希）和 `--audio` 参数（预录音频文件路径），通过 Gateway 声纹注册 API 直接注册声纹（绕过 UI 录音流程），声纹注册成功后设 status=1；由管理员在 UI 上创建其他测试账户 in `backend/apps/users/management/commands/reset_all_data.py`
- [X] T056 更新 users 模块 CLAUDE.md + 创建前端 members 组件目录 CLAUDE.md：添加新增字段、API、服务、任务说明 in `backend/apps/users/CLAUDE.md`；创建组件说明 in `frontend/src/components/members/CLAUDE.md`
- [X] T057 [P] 更新根 CLAUDE.md：015 特性状态从"🚧 进行中"改为"✅ 已完成" in `CLAUDE.md`
- [X] T058 执行 quickstart.md 验收清单：8 项验收场景逐一验证 + SC-001 成员创建 < 60s 计时 + SC-002 用户切换刷新 < 3s 计时 + SC-003 声纹注册 < 60s 计时 + SC-005 切换后数据隔离 100% 正确性验证 + 性能指标验收：成员管理 API p95 < 300ms（手动 curl 计时验证，家庭 <10 用户无需自动化压测）

### Tests for Phase 8

- [X] T059 测试 reset_all_data management command：空库执行不报错、重复执行幂等、管理员账户创建成功（username=anlin, member_type=member, status=1）、SpeakerProfile 关联正确 in `backend/tests/users/test_reset_all_data.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — 立即开始
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — MVP 核心
- **US2 (Phase 4)**: Depends on Phase 2 + US1 MemberSwitchModal（复用同一模态框）
- **US3 (Phase 5)**: Depends on Phase 2 + US1.T027（T043 需验证头像按钮对 guest 不可见，依赖 T027 实现）— 后端 Celery 任务 T041/T042 可与 US1 并行，但前端 T043 需等 T027 完成
- **US4 (Phase 6)**: Depends on US1（CreateMemberWizard Step 1 必须先完成）
- **US5 (Phase 7)**: Depends on Phase 2 — 可与其他 US 并行（纯前端修改）
- **Polish (Phase 8)**: Depends on all user stories complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational)
                       │
                       ├──→ US1 (P1) ──→ US2 (P1) ──→ US4 (P2)
                       │         │
                       │         └──→ US3.T043 (前端验证依赖 T027)
                       │
                       ├──→ US3.T041-T042 (后端 Celery 独立，可与 US1 并行)
                       │
                       └──→ US5 (P3) (独立，可与 US1-US4 并行)
```

### Critical Path

```
Setup → Foundational → US1 → US2 → US4 → Polish
```

### Parallel Opportunities

**Within Phase 2 (Foundational)**:
```
T007 (Repository) ‖ T008 (Frontend types) ‖ T009 (memberStore) ‖ T010 (memberService)
```

**Within Phase 3 (US1)**:
```
T021 (MemberService) ‖ T022 (Serializers)  → T023 (Views) → T024 (URLs)
T025 (Modal) ‖ T026 (Wizard)               → T027 (MessageInput) → T028 (ChatPage)
```

**Cross-story parallel**:
```
US3 (Celery task) can run in parallel with US1/US2
US5 (ambient mode) can run in parallel with US1-US4
```

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Backend tasks in parallel:
Task T004: "扩展 TokenAuthMiddleware in backend/apps/common/middleware.py"
Task T007: "扩展 UserRepository in backend/apps/users/repositories.py"

# Frontend tasks in parallel (after T008):
Task T009: "创建 memberStore in frontend/src/stores/memberStore.ts"
Task T010: "创建 memberService in frontend/src/services/memberService.ts"
```

## Parallel Example: Phase 3 (US1)

```bash
# Backend implementation in parallel:
Task T021: "MemberService in backend/apps/users/services.py"
Task T022: "Serializers in backend/apps/users/serializers.py"

# Frontend components in parallel:
Task T025: "MemberSwitchModal in frontend/src/components/members/MemberSwitchModal.tsx"
Task T026: "CreateMemberWizard in frontend/src/components/members/CreateMemberWizard.tsx"
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Setup (模型 + 迁移)
2. Complete Phase 2: Foundational (中间件 + 登录 + 前端基础)
3. Complete Phase 3: US1 (成员 CRUD + 模态框)
4. Complete Phase 4: US2 (切换视角)
5. **STOP and VALIDATE**: 测试成员管理和切换功能
6. Deploy/demo MVP

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. US1 + US2 → 成员管理 + 切换 (MVP!)
3. US3 → 访客过期自动处理
4. US4 → 声纹注册完整流程
5. US5 → 语音模式统一
6. Polish → 数据重置 + 文档 + 验收

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story (US1-US5)
- 后端代码修改后需 `./scripts/services.sh restart` 重启服务
- 前端代码修改后需 `npm run build` 再 `npm run start -- -p 3784`
- 声纹注册在用户创建时由后端 MemberService 内部调用 Gateway API 完成（不通过 voice/views.py 端点），US4 前端仅需录音组件 + 向导集成提交
- X-Target-User-Id 仅影响聊天/媒体 API（不影响 voice API）。验证失败（目标用户不存在/已过期/已禁用）时返回 400 错误（TARGET_USER_INVALID），向用户展示具体失败原因（宪法 1.4 显式失败原则）。前端收到错误后自动清除 targetUserId 并恢复自身视角
- 用户创建为原子操作：前端分步收集数据（不提前写数据库），后端单请求内完成声纹注册+用户入库，确保不存在 status=0 的"幽灵用户"（status=0 仅用于过期访客）
- 测试任务遵循宪法第三条覆盖率要求：服务层 ≥ 95%，API 视图层 ≥ 80%，仓库层 ≥ 85%，前端组件 ≥ 75%，Hooks ≥ 85%
- 系统不变量（FR-013 无声纹用户不存在、FR-012 用户不可删除）由前后端统一错误处理机制保障，不作为功能开发
