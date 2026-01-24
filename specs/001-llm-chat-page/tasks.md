# Tasks: 大模型聊天页面

**Input**: Design documents from `/specs/001-llm-chat-page/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/openapi.yaml, research.md, quickstart.md

**Tests**: 测试任务已集成在各Phase中（T032a/b、T049c-g、T064-T070），服务层覆盖率要求≥95%，总体≥80%。

**Organization**: 任务按用户故事组织，支持独立实现和测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 任务所属用户故事（US1, US2, US3, US4）

## 模型文档参考

> ⚠️ **强制要求**：实现每个任务前，**必须**阅读下方引用的模型文档章节。

| 文档 | 用途 |
|------|------|
| [data-model.md](./data-model.md) | 数据库表结构、Redis键格式、TTL配置 |
| [behavior-model.md](./behavior-model.md) | 原子业务动作定义、代码模板 |
| [process-model.md](./process-model.md) | 业务流程图、时序图、异常处理 |

## Path Conventions

- **后端**: `backend/` (Django REST Framework)
- **前端**: `frontend/` (Next.js)

---

## Phase 1: Setup (项目初始化)

**Purpose**: 创建项目结构，配置开发环境

- [ ] T001 创建后端 Django 项目结构 `backend/`，配置 Django REST Framework
- [ ] T002 创建前端 Next.js 项目结构 `frontend/`，配置 TypeScript 严格模式
- [ ] T003 [P] 配置后端依赖 `backend/requirements.txt`（Django、LangGraph、gmssl、captcha 等）
- [ ] T004 [P] 配置前端依赖 `frontend/package.json`（react-markdown、mermaid、zustand 等）
- [ ] T005 [P] 创建 Docker Compose 配置 `docker-compose.yml`（PostgreSQL、Redis、Langfuse）
- [ ] T006 [P] 配置环境变量模板
  - 后端：`.env.example`（DATABASE_URL, REDIS_URL, LLM_API_BASE, SECRET_KEY 等）
  - 前端：`frontend/.env.local.example`（NEXT_PUBLIC_API_BASE_URL 等）
  > 📖 参考：[data-model.md#七、配置参数汇总](./data-model.md#七配置参数汇总)

---

## Phase 2: Foundational (基础设施)

**Purpose**: 核心基础设施，所有用户故事的前置条件

**⚠️ CRITICAL**: 用户故事实现必须等待本阶段完成

### 2.1 数据库与迁移

- [ ] T007 创建 Django settings 配置 `backend/core/settings.py`（PostgreSQL、Redis 连接）
  > 📖 参考：[data-model.md#七、配置参数汇总](./data-model.md#七配置参数汇总)
- [ ] T008 创建 Redis 连接管理 `backend/core/redis.py`
  > 📖 参考：[data-model.md#三、Redis缓存设计](./data-model.md#三redis-缓存设计)
- [ ] T009 创建用户模型 `backend/apps/users/models.py`（sys_user 表）
  > 📖 **必读**：[data-model.md#2.1 用户表（sys_user）](./data-model.md#21-用户表sys_user) - 包含完整字段定义、初始数据
- [ ] T010 创建消息模型 `backend/apps/chat/models.py`（message、langgraph_execution 表）
  > 📖 **必读**：[data-model.md#2.2 消息表（message）](./data-model.md#22-消息表message) + [#2.3 执行监控表](./data-model.md#23-执行监控表langgraph_execution--可选)
- [ ] T011 生成并执行数据库迁移

### 2.2 通用组件

- [ ] T012 [P] 创建自定义异常类 `backend/apps/common/exceptions.py`
  - 认证异常：AuthFailedException、TokenExpiredException、AccountLockedException、CaptchaInvalidException
  - LLM异常（宪法4.3）：LLMConnectionError、LLMTimeoutError、LLMRateLimitError、LLMContentFilterError、LLMInvalidResponseError、LLMQuotaExceededError
  > 📖 参考：[behavior-model.md#1.2 用户登录](./behavior-model.md#12-用户登录b_auth_002) - 异常类型定义
  > 📖 参考：[constitution.md#4.3](../../.specify/memory/constitution.md) - 大模型异常处理策略
- [ ] T013 [P] 创建统一响应格式 `backend/apps/common/responses.py`（code/data/message 结构）
- [ ] T014 [P] 创建国密算法封装 `backend/apps/users/crypto.py`（SM3哈希、SM4加密/解密）
  > 📖 参考：[behavior-model.md#1.2 用户登录](./behavior-model.md#12-用户登录b_auth_002) - SM4解密密码、SM3比对哈希流程
- [ ] T015 创建认证与频率限制中间件 `backend/apps/common/middleware.py`
  - Token鉴权：实现R_TOKEN_003双重过期规则（24小时绝对过期 + 1小时无操作过期）
  - 频率限制：匿名100次/时，认证1000次/时，LLM 60次/分
  > 📖 **必读**：[behavior-model.md#1.3 Token鉴权验证](./behavior-model.md#13-token鉴权验证b_auth_003) - 完整验证逻辑
  > 📖 **必读**：[data-model.md#3.1 认证相关](./data-model.md#31-认证相关) - Token缓存结构、TTL计算规则
  > ⚠️ **注意**：Token必须存储在httpOnly Cookie，禁止localStorage
- [ ] T015b [US1] 实现单点登录机制（新登录使旧Token失效，实现R_SSO_001规则）
  - **后端SSE推送登出事件**：新登录时向旧会话推送 `{type: "logout", reason: "SSO_CONFLICT"}` 事件
  - 删除旧Token缓存，更新用户当前Token索引
  > 📖 **必读**：[behavior-model.md#1.4 单点登录Token失效](./behavior-model.md#14-单点登录token失效b_auth_004) - 完整实现逻辑
  > 📖 参考：[rule-model.md#R_SSO_001](./rule-model.md#r_sso_001-单点登录规则) - 单点登录规则定义
  > 📖 参考：[data-model.md#3.1 单点登录Token索引](./data-model.md#31-认证相关) - Redis键格式 `auth:user_token:{user_id}`
- [ ] T015c [US1] 创建SSE事件推送服务 `backend/apps/common/event_service.py`
  - **事件类型**：logout（登出事件，含reason字段）
  - **SSE端点**：GET `/api/v1/events`（需认证，长连接）
  - **推送机制**：通过Redis Pub/Sub实现跨进程事件分发
  > 📖 参考：[process-model.md#一点五、单点登录SSE推送流程](./process-model.md#一点五单点登录sse推送流程p_auth_001a) - 完整流程图
- [ ] T015d [P] [US1] 实现单点登录前端处理 `frontend/src/hooks/useAuth.ts`
  - **监听SSE登出事件**：建立SSE连接监听 `/api/v1/events`，接收服务端推送的登出事件
  - 收到 `SSO_CONFLICT` 事件时显示 Toast 提示："您已在其他设备登录"（停留 3 秒）
  - Toast 消失后自动跳转登录页
  > 📖 参考：[process-model.md#一点五、单点登录SSE推送流程](./process-model.md#一点五单点登录sse推送流程p_auth_001a) - SSE事件格式
  > 📖 参考：[rule-model.md#R_SSO_001](./rule-model.md#r_sso_001-单点登录规则) - 前端检测逻辑

### 2.3 前端基础

- [ ] T016 [P] 配置 Axios 实例 `frontend/src/services/api.ts`（401 拦截跳转登录页）
  > 📖 参考：[process-model.md#二、Token鉴权流程](./process-model.md#二token鉴权流程p_auth_002) - 401响应时前端处理代码
  > ⚠️ **注意**：使用 `credentials: 'include'` 携带httpOnly Cookie
- [ ] T017 [P] 创建 TypeScript 类型定义 `frontend/src/types/index.ts`
- [ ] T018 [P] 创建 401 错误页面 `frontend/src/app/401/page.tsx`（蓝白风格）

**Checkpoint**: 基础设施就绪，用户故事实现可以开始

---

## Phase 3: User Story 1 - 用户登录认证 (Priority: P1) 🎯 MVP

**Goal**: 实现完整的登录认证流程，包括验证码、国密加密、Token 双重过期机制

**Independent Test**: 可独立测试登录流程，验证用户名密码、验证码校验、Token 生成与过期

> 📖 **整体流程**：[process-model.md#一、用户登录流程](./process-model.md#一用户登录流程p_auth_001)

### 后端实现

- [ ] T019 [US1] 创建验证码服务 `backend/apps/users/services.py`（CaptchaService：生成、存储、验证）
  > 📖 **必读**：[behavior-model.md#1.1 获取验证码（B_AUTH_001）](./behavior-model.md#11-获取验证码b_auth_001) - 完整代码模板
  > 📖 参考：[data-model.md#3.1 验证码缓存](./data-model.md#31-认证相关) - Redis键格式 `auth:captcha:{id}`，TTL=120秒
- [ ] T020 [US1] 创建用户仓库层 `backend/apps/users/repositories.py`（用户查询、锁定状态更新）
  > 📖 参考：[data-model.md#2.1 用户表](./data-model.md#21-用户表sys_user) - 字段定义
- [ ] T021 [US1] 扩展认证服务 `backend/apps/users/services.py`（AuthService：密码验证、Token生成、失败锁定，依赖T019）
  - **Token生成格式**：`SM4({username}|{password}|{captcha}|{timestamp})`（见R_TOKEN_001）
  - **必须实现登录失败锁定逻辑**：连续5次失败后锁定账户15分钟（R_LOGIN_001）
  - 锁定状态检查、失败计数递增、锁定时间设置、成功后重置计数
  > 📖 **必读**：[behavior-model.md#1.2 用户登录（B_AUTH_002）](./behavior-model.md#12-用户登录b_auth_002) - 完整登录逻辑代码模板（含锁定处理）
  > 📖 **必读**：[rule-model.md#R_LOGIN_001](./rule-model.md#r_login_001-登录失败锁定规则) - 登录失败锁定规则
  > 📖 **必读**：[rule-model.md#R_TOKEN_001](./rule-model.md#r_token_001-token生成规则) - Token生成规则（含captcha防重放）
  > 📖 参考：[data-model.md#3.1 登录失败计数](./data-model.md#31-认证相关) - Redis键格式 `auth:fail:{username}`，TTL=900秒
  > ⚠️ **安全要求**：Token通过httpOnly Cookie返回，禁止localStorage存储
- [ ] T022 [P] [US1] 创建认证序列化器 `backend/apps/users/serializers.py`（LoginRequest、CaptchaResponse）
- [ ] T023 [US1] 创建认证视图 `backend/apps/users/views.py`（GET /captcha、POST /login、POST /logout）
  > 📖 参考：[process-model.md#一、用户登录流程](./process-model.md#一用户登录流程p_auth_001) - 完整时序图和异常处理表
- [ ] T024 [US1] 配置认证路由 `backend/apps/users/urls.py`
- [ ] T025 [US1] 创建 admin 初始化命令 `backend/apps/users/management/commands/init_admin.py`
  > 📖 参考：[data-model.md#2.1 初始化数据](./data-model.md#21-用户表sys_user) - admin用户初始密码 `!9871229Qing`

### 前端实现

- [ ] T026 [P] [US1] 创建认证服务 `frontend/src/services/authService.ts`（getCaptcha、login、logout）
  > ⚠️ **安全要求**：Token由后端通过httpOnly Cookie设置，前端使用 `credentials: 'include'`
- [ ] T027 [P] [US1] 创建认证 Hook `frontend/src/hooks/useAuth.ts`（登录状态管理、Token 刷新事件监听）
  > 📖 参考：[process-model.md#二、Token鉴权流程](./process-model.md#二token鉴权流程p_auth_002) - 401响应处理逻辑
- [ ] T028 [P] [US1] 创建验证码组件 `frontend/src/components/auth/CaptchaImage.tsx`（实现R_CAPTCHA_003规则：110秒自动刷新间隔）
  - **自动刷新**：验证码过期前10秒（110秒间隔）自动调用API刷新（覆盖spec.md Edge Case "验证码过期"）
  - **手动刷新**：用户点击验证码图片可手动刷新
  > 📖 参考：[data-model.md#3.1 验证码缓存](./data-model.md#31-认证相关) - 验证码TTL=120秒，前端需110秒刷新
  > 📖 参考：[rule-model.md#R_CAPTCHA_003](./rule-model.md#r_captcha_003-验证码自动刷新规则) - 前端自动刷新规则
- [ ] T029 [US1] 创建登录表单组件 `frontend/src/components/auth/LoginForm.tsx`（用户名、密码、验证码）
  > 📖 参考：[process-model.md#异常处理](./process-model.md#异常处理) - 登录异常场景和前端处理
- [ ] T030 [US1] 创建登录页面 `frontend/src/app/login/page.tsx`
- [ ] T031 [US1] 实现路由保护中间件 `frontend/src/middleware.ts`（未登录跳转登录页）
  > 📖 参考：[process-model.md#一、用户登录流程](./process-model.md#一用户登录流程p_auth_001) - 步骤1-3：检查Token跳转
- [ ] T032 [US1] 实现用户活动监听器 `frontend/src/hooks/useActivityTracker.ts`
  - **用户事件定义**（引用spec.md US1场景2）：
    - 包括：页面点击、API请求、页面刷新、浏览器回退等
    - 不包括：系统响应（如大模型完成回复）
  - 检测到用户活动时调用后端API刷新Token TTL
  > 📖 参考：[behavior-model.md#1.3 Token鉴权验证](./behavior-model.md#13-token鉴权验证b_auth_003) - TTL刷新逻辑（不超过24小时边界）
  > 📖 参考：[rule-model.md#R_TOKEN_003](./rule-model.md#r_token_003-token双重过期规则) - 用户活动定义和双重过期机制

### US1 测试（在Checkpoint后执行）

- [ ] T032a [US1] 添加认证服务单元测试 `backend/tests/users/test_services.py`
  - **CaptchaService测试**：验证码生成、Redis存储TTL、一次性使用验证
  - **AuthService测试**：密码SM3哈希验证、Token SM4加密生成、双重过期机制
  - **登录锁定测试**：5次失败锁定、15分钟解锁、成功后计数重置
  - **单点登录测试**：新登录使旧Token失效、Token索引更新
  - **频率限制测试**（覆盖spec.md Edge Cases "频率限制超限"）：
    - 匿名用户100次/时限制（超限返回429，提示"请求过于频繁，请稍后重试"）
    - 认证用户1000次/时限制（超限返回429）
    - LLM接口60次/分限制（超限返回429，含剩余等待时间）
    - 限制计数器TTL验证（Redis键过期后计数重置）
  - **SC-001 登录流程耗时测试**：
    - 测量范围：用户点击登录按钮 → 成功跳转聊天页面
    - 包含环节：验证码校验、密码验证、Token生成、Cookie设置、页面跳转
    - 目标值：< 30秒（前端E2E测量）
    - 工具：Playwright performance.now() 或 Navigation Timing API
  - **init_admin命令测试** `backend/tests/users/test_commands.py`：
    - 验证 admin 用户创建成功
    - 验证初始密码 SM3 哈希正确
    - 验证重复执行幂等性（不报错）
  > ⚠️ **覆盖率要求**：服务层 ≥ 95%（见宪法3.1）
  > ✅ **合格验证**：运行 `pytest --cov=apps.users.services --cov-fail-under=95`，覆盖率低于95%视为任务未完成
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-001 登录流程完成时间指标
- [ ] T032b [P] [US1] 添加登录组件测试 `frontend/tests/components/auth/`（LoginForm、CaptchaImage）

**Checkpoint**: User Story 1 完成，登录认证流程可独立测试

---

## Phase 4: User Story 2 - 发送消息并获取 AI 流式响应 (Priority: P1)

**Goal**: 实现聊天核心功能，包括消息发送、LangGraph Agent 执行、SSE 流式响应、Markdown/Mermaid 渲染

**Independent Test**: 使用已登录用户发送消息，验证流式响应、消息持久化、历史加载

> 📖 **整体流程**：[process-model.md#三、消息发送与流式响应流程](./process-model.md#三消息发送与流式响应流程p_chat_001)

### 后端实现

- [ ] T033 [US2] 创建消息仓库层 `backend/apps/chat/repositories.py`（消息保存、历史查询、用户数据隔离）
  - **消息排序规则（必须遵守）**：
    - 用户消息（role=user）：按后端LangGraph对话Agent接收时间排序
    - AI回复（role=assistant）：按后端回复的首个token生成时间排序
    - 整体按时间顺序正序展示（升序）
  > 📖 **必读**：[data-model.md#2.2 消息表（message）](./data-model.md#22-消息表message) - 完整字段定义、索引设计
  > 📖 参考：[behavior-model.md#2.3 加载历史消息](./behavior-model.md#23-加载历史消息b_chat_003) - 查询逻辑代码模板
- [ ] T034 [US2] 创建 LangGraph Agent 定义 `backend/apps/chat/agent.py`（ReAct Agent、Redis Checkpointer）
  > 📖 **必读**：[data-model.md#五、LangGraph RedisSaver 配置](./data-model.md#五langgraph-redissaver-配置) - Checkpointer初始化代码
  > 📖 **必读**：[behavior-model.md#2.2 执行LangGraph Agent](./behavior-model.md#22-执行langgraph-agentb_chat_002) - 完整Agent执行代码模板
  > 📖 参考：[data-model.md#3.2 LangGraph Checkpoint](./data-model.md#32-langgraph-checkpoint由redissaver管理) - Redis键格式、TTL配置
- [ ] T035 [US2] 创建聊天服务 `backend/apps/chat/services.py`（消息处理、Agent 执行、流式响应生成）
  > 📖 **必读**：[behavior-model.md#2.1 发送消息并获取响应](./behavior-model.md#21-发送消息并获取响应b_chat_001) - send_message代码模板
  > 📖 **必读**：[behavior-model.md#2.2 执行LangGraph Agent](./behavior-model.md#22-执行langgraph-agentb_chat_002) - execute_agent完整代码（含双写策略）
  > 📖 参考：[behavior-model.md#四、Redis Checkpoint 关键说明](./behavior-model.md#四redis-checkpoint-关键说明) - 双写策略解释
- [ ] T036 [P] [US2] 创建聊天序列化器 `backend/apps/chat/serializers.py`（ChatRequest含maxLength=4000验证、MessageVO）
- [ ] T037 [US2] 创建聊天视图 `backend/apps/chat/views.py`（POST /chat SSE流式、GET /messages、POST /stop）
  > 📖 参考：[process-model.md#后端SSE端点](./process-model.md#核心代码) - SSE StreamingResponse代码
- [ ] T038 [US2] 配置聊天路由 `backend/apps/chat/urls.py`
- [ ] T039 [US2] 实现停止生成功能
  - **后端**：接收停止请求，保存当前checkpoint（状态terminated），更新消息status=3（中断）
  - **前端协作**：T046的停止按钮调用此API，响应后T045的MessageList显示"[已中断]"标记 + "继续生成"按钮
  - **提示弹窗**：停止后弹出"响应已中断，如有需要请复制已显示内容"
  > 📖 参考：[data-model.md#2.2 消息表](./data-model.md#22-消息表message) - status字段定义（0-失败,1-正常,2-生成中,3-中断）
- [ ] T039a [US2] 实现"继续生成"功能
  - **后端 POST /chat/resume**：接收message_id，从对应checkpoint恢复生成
  - **前端逻辑**：
    - 点击"继续生成"按钮：调用resume API，从中断处继续生成，更新消息内容
    - 若用户在输入框输入新问题并发送：该中断的checkpoint作废，基于新消息创建新对话轮次
  - **Checkpoint处理**：恢复时使用terminated状态的checkpoint继续对话
  > 📖 参考：[spec.md US2场景9](./spec.md) - 继续生成按钮逻辑
  > 📖 参考：[rule-model.md#R_STREAM_001](./rule-model.md#r_stream_001-流式响应中断处理规则) - Checkpoint状态说明

### 前端实现

- [ ] T040 [P] [US2] 创建聊天状态管理 `frontend/src/stores/chatStore.ts`（Zustand：消息列表、加载状态）
- [ ] T041 [P] [US2] 创建聊天服务 `frontend/src/services/chatService.ts`（sendMessage、getMessages、stopGeneration）
  > ⚠️ **注意**：使用 `credentials: 'include'` 携带httpOnly Cookie
- [ ] T042 [US2] 创建 SSE 流式聊天 Hook `frontend/src/hooks/useChatStream.ts`（流式接收、实时更新；刷新页面时检测进行中消息并自动重连SSE继续接收，见spec.md US2场景5）
  - **消息状态检测**：参考 [data-model.md#2.2 消息表](./data-model.md#22-消息表message) status字段定义（0-失败,1-正常,2-生成中,3-中断）
  - status=2（生成中）时自动重连SSE继续接收
  > 📖 **必读**：[process-model.md#前端SSE处理](./process-model.md#核心代码) - useChatStream完整代码模板
- [ ] T043 [P] [US2] 创建 Markdown 渲染组件 `frontend/src/components/chat/MarkdownRenderer.tsx`（react-markdown、代码高亮）
- [ ] T044 [P] [US2] 创建 Mermaid 渲染组件 `frontend/src/components/chat/MermaidRenderer.tsx`（流式完成后渲染）
- [ ] T045 [US2] 创建消息列表组件 `frontend/src/components/chat/MessageList.tsx`
  - 历史消息渲染（用户消息右侧蓝底、AI消息左侧灰底）
  - 滚动锚定（默认锚定最底部，向上滚动加载更多）
  - **消息状态渲染**（参考 [data-model.md#2.2 消息表](./data-model.md#22-消息表message) status字段）：
    - status=2（生成中）：显示加载动画
    - status=3（中断）：消息末尾显示"[已中断]"灰色标记 + "继续生成"按钮
- [ ] T046 [US2] 创建消息输入组件 `frontend/src/components/chat/MessageInput.tsx`
  - **输入校验**：
    - 空消息拦截：`content.trim()` 为空时禁用发送按钮
    - 长度限制：最大 4000 字符，超出时显示字符计数警告（如"4001/4000"红色）
  - **发送按钮**：空闲状态显示，点击触发消息发送
  - **停止按钮**：生成中状态显示（红色圆形停止图标），点击调用 POST /stop 终止生成
  - **状态切换**：通过 chatStore.isGenerating 控制按钮显示
  - **中断后处理**：停止成功后，已生成的消息末尾显示"[已中断]"灰色标记，弹出Toast提示
  > 📖 参考：[rule-model.md#R_MSG_001](./rule-model.md#r_msg_001-消息长度限制规则) - 4000字符限制
  > 📖 参考：[rule-model.md#R_MSG_002](./rule-model.md#r_msg_002-空消息拦截规则) - 空消息拦截
  > 📖 参考：[spec.md US2场景9](./spec.md) - 停止按钮终止生成并保存checkpoint
  > 📖 协作任务：T039(后端停止API)、T045(MessageList中断标记渲染)
- [ ] T047 [US2] 创建聊天页面 `frontend/src/app/chat/page.tsx`（集成所有聊天组件）
- [ ] T048 [US2] 实现消息发送失败处理（失败时：1.保留用户输入在输入框内 2.不在聊天列表生成用户消息框 3.不存储消息到数据库 4.记录失败日志 5.用户可重新点击发送，见spec.md US2场景10）
- [ ] T049 [US2] 实现历史消息分页加载（游标分页、向上滚动加载更多）
  > 📖 参考：[process-model.md#四、历史消息加载流程](./process-model.md#四历史消息加载流程p_chat_002) - 分页加载时序图
  > 📖 参考：[behavior-model.md#2.3 加载历史消息](./behavior-model.md#23-加载历史消息b_chat_003) - 游标分页逻辑
- [ ] T049a [US2] 实现LLM服务异常处理（连接失败、超时、频率限制等，见宪法4.3）
- [ ] T049b [US2] 实现网络中断错误提示组件 `frontend/src/components/chat/NetworkError.tsx`
  - 显示网络错误提示，保留用户输入内容
  - 与 MessageInput 组件集成，失败时阻止消息提交
  > ⚠️ **阻塞依赖**：必须等待 T046 (MessageInput) 完成后再开始
  > 📖 参考：[spec.md Edge Cases](./spec.md) - 网络中断时发送消息场景

### US2 测试（在Checkpoint后执行）

- [ ] T049c [US2] 添加聊天服务单元测试 `backend/tests/chat/test_services.py`
  - **ChatService测试**：消息发送、空消息拦截、4000字符限制
  - **AgentService测试**：Agent执行、流式响应生成、Checkpoint交互
  - **异常处理测试**（宪法4.3）：
    - LLMConnectionError：重试3次逻辑
    - LLMTimeoutError：重试3次逻辑
    - LLMRateLimitError：不重试，返回等待时间
    - LLMContentFilterError：不重试，返回用户修改提示
    - LLMInvalidResponseError：重试3次逻辑
    - LLMQuotaExceededError：不重试，返回联系管理员提示
  - **频率限制测试**：LLM接口60次/分限制（超限返回429）
  - **停止生成测试**：中断时checkpoint保存、消息status=3更新
  > ⚠️ **覆盖率要求**：服务层 ≥ 95%（见宪法3.1）
  > ✅ **合格验证**：运行 `pytest --cov=apps.chat.services --cov-fail-under=95`，覆盖率低于95%视为任务未完成
- [ ] T049d [P] [US2] 添加聊天组件测试 `frontend/tests/components/chat/`
  - **MessageList测试**：消息渲染、滚动锚定、中断标记显示
  - **MessageInput测试**：空消息拦截、4000字符限制、发送/停止按钮切换
  - **MarkdownRenderer测试**：代码块高亮、表格渲染
  - **SC-006 渲染性能冒烟测试**：
    - **Markdown 渲染**（1000字符含代码块）< 500ms
      - 测量起点：调用 MarkdownRenderer 组件
      - 测量终点：useEffect 完成 DOM 更新
    - **Mermaid 图表渲染**（简单流程图）< 500ms
      - 测量起点：检测到 ```mermaid 代码块
      - 测量终点：mermaid.render() 回调完成
    - 工具：`performance.now()` + React Profiler
    - 环境：Jest + @testing-library/react
  - **LLM错误提示测试**：
    - 连接失败提示："AI 服务暂时无法连接，请稍后重试"
    - 超时提示："AI 响应超时，请稍后重试"
    - 频率限制提示："请求过于频繁，请稍后重试"
    - 内容过滤提示："消息包含敏感内容，请修改后重试"
    - 配额用尽提示："服务配额用尽，请联系管理员"
  - **API频率限制测试（429响应）**：
    - 匿名用户超限提示："请求过于频繁，请稍后重试"
    - 认证用户超限提示："请求过于频繁，请稍后重试"
    - LLM接口超限提示："请求过于频繁，请稍后重试"（含剩余等待时间）
  > 📖 参考：[constitution.md#4.3](../../.specify/memory/constitution.md) - LLM异常用户提示
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-006 渲染性能指标（完整压测在 T070）
- [ ] T049e [US2] 添加登录到聊天端到端测试 `frontend/tests/e2e/login-to-chat.spec.ts`（Playwright完整流程）
  - **SC-007 认证拦截验证**：未登录访问聊天页自动跳转登录页（100%拦截）
  - **SC-008 消息持久化验证**：发送消息后刷新页面，验证消息仍存在（100%成功率）
  - **SC-009 用户数据隔离验证**：用户A发送消息，用户B登录后验证看不到用户A的消息
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-007~009 验收标准
- [ ] T049f [P] [US2] 添加基础并发冒烟测试 `backend/tests/chat/test_concurrency.py`
  - **SC-004 初步验证**：10 用户并发发送消息，验证无死锁/数据错乱
  - 测试场景：并发登录、并发消息发送、并发历史加载
  - 工具：pytest-asyncio + httpx
  - 完整负载测试（100用户）延迟到 Phase 7 T069
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-004 并发用户支持指标
- [ ] T049g [P] [US2] 添加基础性能冒烟测试 `backend/tests/performance/test_smoke.py`
  - **SC-002 初步验证**：大模型首令牌延迟 < 3秒（开发环境允许50%误差）
  - **SC-003 初步验证**：流式字符延迟 < 200ms（开发环境允许100%误差）
  - **SC-005 初步验证**：历史消息加载（50条）< 3秒
  - 目的：确保架构设计不存在根本性性能问题
  - 工具：pytest + httpx（不需要 locust）
  - 完整性能测试延迟到 Phase 7 T068-T070
  > ⚠️ **阻塞条件**：如果冒烟测试失败，需在继续开发前排查性能瓶颈
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-002, SC-003, SC-005

**Checkpoint**: User Stories 1 AND 2 完成，核心聊天功能可用

---

## Phase 5: User Story 3 - 系统配置管理 (Priority: P2)

**Goal**: 通过配置文件统一管理数据库、Redis、LLM 等配置项

**Independent Test**: 修改配置文件参数，验证系统正确读取和应用新配置

- [ ] T050 [US3] 完善 Django settings 配置结构（环境变量、配置分层）
  > 📖 参考：[data-model.md#七、配置参数汇总](./data-model.md#七配置参数汇总) - 完整配置结构
- [ ] T051 [P] [US3] 创建 LLM 服务配置模块 `backend/core/llm_config.py`（API地址、模型名称、超时配置）
- [ ] T052 [P] [US3] 创建 LangGraph Checkpoint 配置模块 `backend/core/checkpoint_config.py`（TTL、refresh_on_read）
  > 📖 **必读**：[data-model.md#五、LangGraph RedisSaver 配置](./data-model.md#五langgraph-redissaver-配置) - TTL配置代码
  > 📖 参考：[data-model.md#3.2 LangGraph Checkpoint](./data-model.md#32-langgraph-checkpoint由redissaver管理) - TTL规则：24小时过期、读取时刷新
- [ ] T053 [US3] 配置验证机制（启动时检查必要配置项）

**Checkpoint**: 配置管理完成，系统可通过环境变量灵活配置

---

## Phase 6: User Story 4 - LangGraph Agent 监控 (Priority: P3)

**Goal**: 集成 Langfuse 监控，支持 LangGraph Dev 调试

**Independent Test**: 发送测试消息，在 Langfuse 界面验证调用记录和指标

- [ ] T054 [US4] 集成 Langfuse 监控 `backend/apps/chat/services.py`（trace_id、调用指标记录）
  - **降级处理**：Langfuse连接失败时记录警告日志，不影响聊天主流程
  - 使用try-except包裹Langfuse初始化和回调，确保监控故障不阻塞业务
  > 📖 参考：[behavior-model.md#2.2 执行LangGraph Agent](./behavior-model.md#22-执行langgraph-agentb_chat_002) - Langfuse CallbackHandler集成代码
- [ ] T055 [P] [US4] 创建执行监控仓库 `backend/apps/chat/repositories.py`（langgraph_execution 表操作）
  > 📖 **必读**：[data-model.md#2.3 执行监控表（langgraph_execution）](./data-model.md#23-执行监控表langgraph_execution--可选) - 完整字段定义
- [ ] T056 [US4] 实现执行详情记录（节点执行、Token 统计、错误信息）
  > 📖 参考：[behavior-model.md#2.2 执行LangGraph Agent](./behavior-model.md#22-执行langgraph-agentb_chat_002) - node_executions、token统计代码
- [ ] T057 [US4] 配置 LangGraph Dev 支持（langgraph.json）

**Checkpoint**: 所有用户故事完成，系统功能完整

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨功能优化、安全加固、文档更新

- [ ] T058 [P] 添加健康检查端点 `backend/apps/common/views.py`（/health/live、/health/ready）
- [ ] T059 [P] 配置 CORS 和安全头 `backend/core/settings.py`
- [ ] T060 [P] 添加请求日志中间件 `backend/apps/common/middleware.py`
- [ ] T061 [P] 前端 UI 组件库配置 `frontend/src/components/ui/`（基础按钮、输入框、提示等）
- [ ] T062 代码风格检查配置（后端 Black/isort，前端 ESLint/Prettier）
- [ ] T063 运行 quickstart.md 验证，确保启动流程正常
- [ ] T064 [P] 补充后端测试覆盖率 `backend/tests/`（仓库层、视图层，确保总体 ≥ 80%）
- [ ] T065 [P] 补充前端测试覆盖率 `frontend/tests/`（Hooks、工具函数，确保总体 ≥ 80%）
- [ ] T066 验证测试覆盖率达标（运行覆盖率报告，确保符合宪法3.1要求）
- [ ] T067 [US2] 实现 Checkpoint 故障恢复机制 `backend/apps/chat/services.py`（Redis重启后从PostgreSQL重建对话历史）
  > 📖 **必读**：[process-model.md#八、Checkpoint故障恢复策略](./process-model.md#八checkpoint故障恢复策略) - rebuild_checkpoint_if_needed完整代码
  > 📖 参考：[data-model.md#六、数据流说明](./data-model.md#六数据流说明) - 数据一致性策略：Checkpoint丢失可从Message重建
- [ ] T068 [P] 添加后端性能测试 `backend/tests/performance/`
  - **SC-002 验证**：大模型首令牌延迟 < 2秒
  - **SC-003 验证**：流式字符延迟 < 100ms（相邻 chunk 间隔）
  - **SC-005 验证**：历史消息加载（50条）< 2秒
  - 工具：pytest-benchmark 或 locust
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-002, SC-003, SC-005 指标定义
- [ ] T069 [P] 添加并发负载测试 `backend/tests/load/`
  - **SC-004 验证**：并发用户 ≥ 100
  - 测试场景：100 用户同时发送消息，验证系统稳定性
  - 工具：locust
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-004 并发用户支持指标
- [ ] T070 [P] 添加前端性能压力测试 `frontend/tests/performance/`
  - **SC-006 完整验证**：
    - 大文档 Markdown 渲染（10000字符、复杂嵌套）< 500ms
    - 多图表 Mermaid 渲染（3个流程图同时渲染）< 500ms
    - 测量方式：Playwright page.evaluate() + Performance API
    - 测量起点：模拟 SSE done 事件触发
    - 测量终点：MutationObserver 检测 DOM 稳定
  - 边界场景：深度嵌套列表、超大表格、代码块语法高亮
  - 与 T049d 冒烟测试互补，验证边界场景
  > 📖 参考：[spec.md#Success Criteria](./spec.md) - SC-006 渲染性能指标（完整定义）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖，可立即开始
- **Phase 2 (Foundational)**: 依赖 Phase 1 完成 - **阻塞所有用户故事**
- **Phase 3 (US1)**: 依赖 Phase 2 完成
- **Phase 4 (US2)**: 依赖 Phase 2 完成；后端可与 Phase 3 并行开发，前端需登录态故建议先完成 US1
- **Phase 5 (US3)**: 依赖 Phase 2 完成，可与 Phase 3/4 并行
- **Phase 6 (US4)**: 依赖 Phase 4 (US2) 完成（需要 Agent 执行基础）
- **Phase 7 (Polish)**: 依赖所有用户故事完成

### User Story Dependencies

```
Phase 2 (Foundational)
        │
        ├───────────────────────────────────────┐
        │                                       │
        ▼                                       ▼
   Phase 3 (US1)                         Phase 5 (US3)
   用户登录认证                            系统配置管理
        │
        ▼
   Phase 4 (US2)  ◄─────────────────────────────┘
   聊天核心功能     (US3 提供的配置被 US2 使用)
        │
        ▼
   Phase 6 (US4)
   Agent 监控
        │
        ▼
   Phase 7 (Polish)
```

### Within Each User Story

- 仓库层 (repositories) → 服务层 (services) → 序列化器 (serializers) → 视图 (views)
- 后端 API 完成后再实现对应前端页面

### Task-Level Blocking Dependencies

- **T049b** 阻塞于 **T046**：网络错误组件需集成到 MessageInput 组件

### Parallel Opportunities

**Phase 2 并行任务**:
```bash
Task: T012 创建自定义异常类 backend/apps/common/exceptions.py
Task: T013 创建统一响应格式 backend/apps/common/responses.py
Task: T014 创建国密算法封装 backend/apps/users/crypto.py
```

**US1 前端并行任务**:
```bash
Task: T026 创建认证服务 frontend/src/services/authService.ts
Task: T027 创建认证 Hook frontend/src/hooks/useAuth.ts
Task: T028 创建验证码组件 frontend/src/components/auth/CaptchaImage.tsx
```

**US2 前端并行任务**:
```bash
Task: T040 创建聊天状态管理 frontend/src/stores/chatStore.ts
Task: T041 创建聊天服务 frontend/src/services/chatService.ts
Task: T043 创建 Markdown 渲染组件 frontend/src/components/chat/MarkdownRenderer.tsx
Task: T044 创建 Mermaid 渲染组件 frontend/src/components/chat/MermaidRenderer.tsx
```

---

## Implementation Strategy

### MVP First (仅 User Story 1 + 2)

1. 完成 Phase 1: Setup
2. 完成 Phase 2: Foundational (**CRITICAL**)
3. 完成 Phase 3: User Story 1 (登录认证)
4. **STOP and VALIDATE**: 独立测试登录流程
5. 完成 Phase 4: User Story 2 (聊天功能)
6. **STOP and VALIDATE**: 测试完整聊天流程
7. 部署/演示 MVP

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. Add US1 → 测试登录 → 可演示登录功能
3. Add US2 → 测试聊天 → **MVP 可用！**
4. Add US3 → 配置管理优化
5. Add US4 → 监控能力增强
6. Polish → 生产就绪

---

## Summary

| 阶段 | 任务数 | 关键产出 |
|------|--------|----------|
| Phase 1: Setup | 6 | 项目结构、依赖配置 |
| Phase 2: Foundational | 15 | 数据库模型、通用组件、中间件（含频率限制）、单点登录、SSE事件推送 |
| Phase 3: US1 登录认证 | 16 | 验证码、登录、Token 机制、**认证测试** |
| Phase 4: US2 聊天功能 | 25 | Agent、流式响应、Markdown 渲染、异常处理、继续生成、**聊天测试+E2E+并发冒烟+性能冒烟** |
| Phase 5: US3 配置管理 | 4 | 配置模块化 |
| Phase 6: US4 监控 | 4 | Langfuse 集成 |
| Phase 7: Polish | 13 | 补充测试覆盖率、安全加固、故障恢复、**性能测试** |
| **总计** | **83** | |

### MVP Scope (推荐)

- Phase 1 + Phase 2 + Phase 3 + Phase 4 = **62 任务**
- 涵盖登录认证 + 聊天核心功能（含继续生成功能、SSE事件推送）
- 可独立部署和演示

### 性能测试 Scope (生产就绪)

- Phase 7 新增 T068/T069/T070 = **3 任务**
- 验证 SC-002~SC-006 成功标准
- 确保生产环境性能达标

---

## Notes

- [P] 标记的任务可并行执行（不同文件，无依赖）
- [Story] 标签将任务映射到具体用户故事
- 每个用户故事应可独立完成和测试
- 在任何 Checkpoint 处停止验证故事独立性
- 每个任务或逻辑组完成后提交代码

### 模型引用图例

- 📖 **必读**：实现前必须阅读的模型文档章节，包含完整代码模板
- 📖 参考：建议阅读的相关章节，提供上下文和设计决策
- ⚠️ **注意**：安全要求或易错点，必须遵守

### 交叉验证矩阵

| 任务范围 | data-model.md | behavior-model.md | process-model.md |
|---------|---------------|-------------------|------------------|
| Phase 2 基础设施 | 2.1, 2.2, 3.1, 五, 七 | 1.2, 1.3 | 二 |
| Phase 3 登录认证 | 2.1, 3.1 | 1.1, 1.2, 1.3 | 一, 二 |
| Phase 4 聊天功能 | 2.2, 3.2, 五 | 2.1, 2.2, 2.3, 四 | 三, 四 |
| Phase 5 配置管理 | 五, 七 | - | - |
| Phase 6 监控 | 2.3 | 2.2 | - |
| Phase 7 Polish | 六 | - | 八 |
