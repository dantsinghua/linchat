# Tasks: 大模型聊天页面

**Input**: Design documents from `/specs/001-llm-chat-page/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/openapi.yaml, research.md, quickstart.md

**Tests**: 未明确要求测试，任务以实现为主，测试作为Polish阶段补充。

**Organization**: 任务按用户故事组织，支持独立实现和测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 任务所属用户故事（US1, US2, US3, US4）

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
- [ ] T006 [P] 配置环境变量模板 `.env.example`

---

## Phase 2: Foundational (基础设施)

**Purpose**: 核心基础设施，所有用户故事的前置条件

**⚠️ CRITICAL**: 用户故事实现必须等待本阶段完成

### 2.1 数据库与迁移

- [ ] T007 创建 Django settings 配置 `backend/core/settings.py`（PostgreSQL、Redis 连接）
- [ ] T008 创建 Redis 连接管理 `backend/core/redis.py`
- [ ] T009 创建用户模型 `backend/apps/users/models.py`（sys_user 表）
- [ ] T010 创建消息模型 `backend/apps/chat/models.py`（message、langgraph_execution 表）
- [ ] T011 生成并执行数据库迁移

### 2.2 通用组件

- [ ] T012 [P] 创建自定义异常类 `backend/apps/common/exceptions.py`（AuthFailedException、TokenExpiredException 等）
- [ ] T013 [P] 创建统一响应格式 `backend/apps/common/responses.py`（code/data/message 结构）
- [ ] T014 [P] 创建国密算法封装 `backend/apps/users/crypto.py`（SM3哈希、SM4加密/解密）
- [ ] T015 创建 Token 鉴权中间件 `backend/apps/common/middleware.py`（实现R_TOKEN_003双重过期规则：24小时绝对过期 + 1小时无操作过期）
- [ ] T015a [P] 创建频率限制中间件 `backend/apps/common/middleware.py`（匿名100次/时，认证1000次/时，LLM 60次/分）
- [ ] T015b [US1] 实现单点登录机制（新登录使旧Token失效）
- [ ] T015c [P] [US1] 实现单点登录前端处理 `frontend/src/hooks/useAuth.ts`（检测Token失效时显示"您已在其他设备登录"提示并跳转登录页）

### 2.3 前端基础

- [ ] T016 [P] 配置 Axios 实例 `frontend/src/services/api.ts`（401 拦截跳转登录页）
- [ ] T017 [P] 创建 TypeScript 类型定义 `frontend/src/types/index.ts`
- [ ] T018 [P] 创建 401 错误页面 `frontend/src/app/401/page.tsx`（蓝白风格）

**Checkpoint**: 基础设施就绪，用户故事实现可以开始

---

## Phase 3: User Story 1 - 用户登录认证 (Priority: P1) 🎯 MVP

**Goal**: 实现完整的登录认证流程，包括验证码、国密加密、Token 双重过期机制

**Independent Test**: 可独立测试登录流程，验证用户名密码、验证码校验、Token 生成与过期

### 后端实现

- [ ] T019 [US1] 创建验证码服务 `backend/apps/users/services.py`（CaptchaService：生成、存储、验证）
- [ ] T020 [US1] 创建用户仓库层 `backend/apps/users/repositories.py`（用户查询、锁定状态更新）
- [ ] T021 [US1] 扩展认证服务 `backend/apps/users/services.py`（AuthService：密码验证、Token生成、失败锁定，依赖T019）
- [ ] T022 [P] [US1] 创建认证序列化器 `backend/apps/users/serializers.py`（LoginRequest、CaptchaResponse）
- [ ] T023 [US1] 创建认证视图 `backend/apps/users/views.py`（GET /captcha、POST /login、POST /logout）
- [ ] T024 [US1] 配置认证路由 `backend/apps/users/urls.py`
- [ ] T025 [US1] 创建 admin 初始化命令 `backend/apps/users/management/commands/init_admin.py`

### 前端实现

- [ ] T026 [P] [US1] 创建认证服务 `frontend/src/services/authService.ts`（getCaptcha、login、logout）
- [ ] T027 [P] [US1] 创建认证 Hook `frontend/src/hooks/useAuth.ts`（登录状态管理、Token 刷新事件监听）
- [ ] T028 [P] [US1] 创建验证码组件 `frontend/src/components/auth/CaptchaImage.tsx`（实现R_CAPTCHA_003规则：110秒自动刷新间隔）
- [ ] T029 [US1] 创建登录表单组件 `frontend/src/components/auth/LoginForm.tsx`（用户名、密码、验证码）
- [ ] T030 [US1] 创建登录页面 `frontend/src/app/login/page.tsx`
- [ ] T031 [US1] 实现路由保护中间件 `frontend/src/middleware.ts`（未登录跳转登录页）
- [ ] T032 [US1] 实现用户活动监听器 `frontend/src/hooks/useActivityTracker.ts`（页面点击、请求、刷新时刷新Token有效期）

**Checkpoint**: User Story 1 完成，登录认证流程可独立测试

---

## Phase 4: User Story 2 - 发送消息并获取 AI 流式响应 (Priority: P1)

**Goal**: 实现聊天核心功能，包括消息发送、LangGraph Agent 执行、SSE 流式响应、Markdown/Mermaid 渲染

**Independent Test**: 使用已登录用户发送消息，验证流式响应、消息持久化、历史加载

### 后端实现

- [ ] T033 [US2] 创建消息仓库层 `backend/apps/chat/repositories.py`（消息保存、历史查询、用户数据隔离；排序规则：用户消息按Agent接收时间、AI回复按首token生成时间，见spec.md US2场景6）
- [ ] T034 [US2] 创建 LangGraph Agent 定义 `backend/apps/chat/agent.py`（ReAct Agent、Redis Checkpointer）
- [ ] T035 [US2] 创建聊天服务 `backend/apps/chat/services.py`（消息处理、Agent 执行、流式响应生成）
- [ ] T036 [P] [US2] 创建聊天序列化器 `backend/apps/chat/serializers.py`（ChatRequest含maxLength=4000验证、MessageVO）
- [ ] T037 [US2] 创建聊天视图 `backend/apps/chat/views.py`（POST /chat SSE流式、GET /messages、POST /stop）
- [ ] T038 [US2] 配置聊天路由 `backend/apps/chat/urls.py`
- [ ] T039 [US2] 实现停止生成功能（checkpoint 保存、状态更新）

### 前端实现

- [ ] T040 [P] [US2] 创建聊天状态管理 `frontend/src/stores/chatStore.ts`（Zustand：消息列表、加载状态）
- [ ] T041 [P] [US2] 创建聊天服务 `frontend/src/services/chatService.ts`（sendMessage、getMessages、stopGeneration）
- [ ] T042 [US2] 创建 SSE 流式聊天 Hook `frontend/src/hooks/useChatStream.ts`（流式接收、实时更新；刷新页面时检测status=2消息并自动重连SSE继续接收，见spec.md US2场景5）
- [ ] T043 [P] [US2] 创建 Markdown 渲染组件 `frontend/src/components/chat/MarkdownRenderer.tsx`（react-markdown、代码高亮）
- [ ] T044 [P] [US2] 创建 Mermaid 渲染组件 `frontend/src/components/chat/MermaidRenderer.tsx`（流式完成后渲染）
- [ ] T045 [US2] 创建消息列表组件 `frontend/src/components/chat/MessageList.tsx`（历史消息、滚动锚定）
- [ ] T046 [US2] 创建消息输入组件 `frontend/src/components/chat/MessageInput.tsx`（发送/停止按钮切换）
- [ ] T047 [US2] 创建聊天页面 `frontend/src/app/chat/page.tsx`（集成所有聊天组件）
- [ ] T048 [US2] 实现消息发送失败处理（失败时：1.保留用户输入在输入框内 2.不在聊天列表生成用户消息框 3.不存储消息到数据库 4.记录失败日志 5.用户可重新点击发送，见spec.md US2场景10）
- [ ] T049 [US2] 实现历史消息分页加载（游标分页、向上滚动加载更多）
- [ ] T049a [US2] 实现LLM服务异常处理（连接失败、超时、频率限制等，见宪法4.3）
- [ ] T049b [P] [US2] 实现网络中断错误提示组件 `frontend/src/components/chat/NetworkError.tsx`

**Checkpoint**: User Stories 1 AND 2 完成，核心聊天功能可用

---

## Phase 5: User Story 3 - 系统配置管理 (Priority: P2)

**Goal**: 通过配置文件统一管理数据库、Redis、LLM 等配置项

**Independent Test**: 修改配置文件参数，验证系统正确读取和应用新配置

- [ ] T050 [US3] 完善 Django settings 配置结构（环境变量、配置分层）
- [ ] T051 [P] [US3] 创建 LLM 服务配置模块 `backend/core/llm_config.py`（API地址、模型名称、超时配置）
- [ ] T052 [P] [US3] 创建 LangGraph Checkpoint 配置模块 `backend/core/checkpoint_config.py`（TTL、refresh_on_read）
- [ ] T053 [US3] 配置验证机制（启动时检查必要配置项）

**Checkpoint**: 配置管理完成，系统可通过环境变量灵活配置

---

## Phase 6: User Story 4 - LangGraph Agent 监控 (Priority: P3)

**Goal**: 集成 Langfuse 监控，支持 LangGraph Dev 调试

**Independent Test**: 发送测试消息，在 Langfuse 界面验证调用记录和指标

- [ ] T054 [US4] 集成 Langfuse 监控 `backend/apps/chat/services.py`（trace_id、调用指标记录）
- [ ] T055 [P] [US4] 创建执行监控仓库 `backend/apps/chat/repositories.py`（langgraph_execution 表操作）
- [ ] T056 [US4] 实现执行详情记录（节点执行、Token 统计、错误信息）
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
- [ ] T064 [P] 添加后端单元测试 `backend/tests/`（服务层核心逻辑）
- [ ] T065 [P] 添加前端组件测试 `frontend/tests/components/`
- [ ] T066 添加端到端测试 `frontend/tests/e2e/`（Playwright 登录到聊天完整流程）

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
| Phase 2: Foundational | 15 | 数据库模型、通用组件、中间件、频率限制、单点登录 |
| Phase 3: US1 登录认证 | 14 | 验证码、登录、Token 机制 |
| Phase 4: US2 聊天功能 | 19 | Agent、流式响应、Markdown 渲染、异常处理 |
| Phase 5: US3 配置管理 | 4 | 配置模块化 |
| Phase 6: US4 监控 | 4 | Langfuse 集成 |
| Phase 7: Polish | 9 | 测试、安全加固、文档 |
| **总计** | **71** | |

### MVP Scope (推荐)

- Phase 1 + Phase 2 + Phase 3 + Phase 4 = **54 任务**
- 涵盖登录认证 + 聊天核心功能
- 可独立部署和演示

---

## Notes

- [P] 标记的任务可并行执行（不同文件，无依赖）
- [Story] 标签将任务映射到具体用户故事
- 每个用户故事应可独立完成和测试
- 在任何 Checkpoint 处停止验证故事独立性
- 每个任务或逻辑组完成后提交代码
