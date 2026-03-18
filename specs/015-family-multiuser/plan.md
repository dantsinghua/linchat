# Implementation Plan: 家庭多用户系统

**Branch**: `015-family-multiuser` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/015-family-multiuser/spec.md`

## Summary

为 LinChat 家庭部署场景增加多用户能力：扩展 SysUser 模型支持成员/访客区分（member_type）、访客过期（guest_expires_at）；成员可通过管理面板创建用户、快速切换操作目标查看其他用户数据；声纹注册作为用户创建必要步骤；Web 端语音模式统一为 ambient。用户一旦创建不可删除；系统中不存在无声纹的用户。

核心技术方案：通过 `X-Target-User-Id` HTTP Header + 中间件设置 `request.target_user_id`，仅聊天相关 API（消息读写 + AI 回复含记忆召回）使用目标用户数据；其他 API 始终使用 `request.user_id`（登录用户）。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**: Django 4.2+ / DRF 3.14+ / uvicorn 0.30+ / Celery 5.3+ (后端) / Next.js 14+ / React 18+ / Zustand (前端)
**Storage**: PostgreSQL 15 (SysUser 扩展字段) / Redis DB0 (Token 信息扩展 member_type) / Redis DB2 (Celery beat)
**Testing**: pytest + pytest-django (后端) / Jest (前端)
**Target Platform**: Linux server (Ubuntu 22.04)
**Project Type**: Web (前后端分离 Monorepo)
**Performance Goals**: 成员管理 API p95 < 300ms / 用户切换后页面刷新 < 3s / 声纹注册全流程 < 60s
**Constraints**: 家庭成员 < 10 人，无需大规模并发
**Scale/Scope**: 新增 ~5 个后端文件、~4 个前端组件、1 个 Celery 定时任务

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法条款 | 状态 | 说明 |
|----------|------|------|
| **1.1 分层架构** | PASS | 新增 views → services → repositories 严格遵守三层 |
| **1.2 接口设计标准** | PASS | REST API `/api/v1/members/`，统一响应格式 |
| **1.3 数据一致性** | PASS | PostgreSQL 唯一数据源，事务保护创建操作 |
| **1.4 简单设计与显式失败** | PASS | TARGET_USER_INVALID 显式错误 + 前端 toast 展示具体原因，禁止静默 fallback |
| **2.1 Python 规范** | PASS | PEP 8 + Black + isort + 类型注解 |
| **2.2 TypeScript 规范** | PASS | ESLint + Prettier + 严格模式 + interface Props |
| **3.1 测试覆盖率** | PASS | 服务层 ≥ 95%，总体 ≥ 80% |
| **4.1 Token 存储** | PASS | Token 仍在 httpOnly Cookie，target_user_id 存 localStorage 是 UI 偏好非安全令牌 |
| **4.1 数据隔离 user_id** | PASS | 特性直接复用现有 user_id 隔离机制 |
| **4.2 密码哈希 SM3** | PASS | 新用户密码使用 SM3 哈希 |
| **4.3 LLM 异常处理** | N/A | 本特性不新增 LLM 调用 |
| **4.4 术语定义** | PASS | 遵守"单用户单会话"定义，切换改变操作目标而非创建新会话 |
| **8.2 ASGI 模式** | PASS | 不变 |
| **9.2 家庭场景单并发用户（多用户档案）** | PASS | 宪法 1.9.0 已更新措辞，明确支持多用户档案 |

**Post-Phase 1 Re-check**: 数据模型和 API 合约设计完成后，确认无新增违规。

## Project Structure

### Documentation (this feature)

```text
specs/015-family-multiuser/
├── spec.md              # 特性规范（已完成）
├── plan.md              # 本文件
├── research.md          # Phase 0 技术调研
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 快速开始
├── contracts/           # Phase 1 API 合约
│   └── members-api.md   # 成员管理 API
└── tasks.md             # Phase 2 任务清单（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── users/
│   │   ├── models.py             # SysUser 扩展：member_type, guest_expires_at
│   │   ├── views.py              # 扩展 MeView + 新增 MemberListCreateView
│   │   ├── services.py           # 扩展 AuthService + 新增 MemberService
│   │   ├── repositories.py       # 扩展 UserRepository（查询活跃成员等）
│   │   ├── serializers.py        # 新增 CreateMemberSerializer, MemberListSerializer
│   │   ├── urls.py               # 新增 /api/v1/members/ 路由
│   │   └── tasks.py              # 新增：访客过期定时任务
│   └── common/
│       └── middleware.py          # 扩展 TokenAuthMiddleware：X-Target-User-Id 处理
├── core/
│   └── celery.py                 # 新增 expire-guests beat schedule
└── tests/
    └── users/
        ├── test_member_service.py # 成员创建服务测试
        ├── test_member_views.py   # 成员管理 API 测试
        └── test_guest_expiry.py   # 访客过期测试

frontend/
├── src/
│   ├── components/
│   │   ├── chat/
│   │   │   └── MessageInput.tsx          # 修改：左侧添加用户切换按钮
│   │   └── members/
│   │       ├── MemberSwitchModal.tsx      # 新增：用户切换模态框
│   │       ├── CreateMemberWizard.tsx     # 新增：创建用户分步引导
│   │       └── VoiceprintRecorder.tsx     # 新增：声纹录音组件
│   ├── stores/
│   │   └── memberStore.ts                # 新增：成员管理状态
│   ├── services/
│   │   ├── api.ts                        # 修改：请求拦截器添加 X-Target-User-Id header
│   │   └── memberService.ts              # 新增：成员管理 API
│   ├── hooks/
│   │   └── useAuth.tsx                   # 修改：扩展 /auth/me 返回 member_type
│   └── types/
│       └── index.ts                      # 修改：扩展 User 类型
```

**Structure Decision**: 复用现有 Web 应用结构。后端在 `users` app 内扩展成员管理（模型、服务、API），不新建 app。前端新增 `components/members/` 目录放置成员管理组件。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(无)* | 宪法 9.2 已更新为 v1.9.0，明确支持"单并发用户系统（多用户档案）" | — |

---

## Phase 0: Research

### R-001: Target User ID 后端传播机制

**Decision**: 使用 `X-Target-User-Id` HTTP Header + 中间件设置独立的 `request.target_user_id` 属性（不覆盖 `request.user_id`）

**Rationale**:
- **精准控制**: `request.user_id` 始终为登录用户，权限校验、记忆管理、语音设置等操作基于登录身份不变
- **显式使用**: 仅聊天相关 API（chat views, agent pipeline）显式读取 `request.target_user_id`，其他 API 无感知
- **安全保障**: 仅 member_type=member 的已认证用户可使用此 Header，目标用户必须存在且未过期
- **无需 auth_user_id**: `request.user_id` 本身就是登录用户，无需额外属性

**实现细节**:
```python
# TokenAuthMiddleware.__call__ 扩展
request.user_id = user_info["user_id"]       # 始终为登录用户，不覆盖

# 检查点 A：过期访客存量 Token 拦截
if user_info.get("member_type") == "guest":
    user = await SysUser.objects.filter(user_id=request.user_id).afirst()
    if user and user.is_guest_expired():
        return JsonResponse({"code": "TOKEN_EXPIRED", ...}, status=401)

# 检查点 B：目标用户解析（显式失败，宪法 1.4）
target = request.META.get("HTTP_X_TARGET_USER_ID")
if target and user_info.get("member_type") == "member":
    # 验证目标用户存在、已激活、未过期，任一不满足返回错误
    target_user = await SysUser.objects.filter(user_id=int(target), status=1).afirst()
    if not target_user or target_user.is_guest_expired():
        return JsonResponse({"code": "TARGET_USER_INVALID", ...}, status=400)
    request.target_user_id = int(target)
else:
    request.target_user_id = request.user_id # 默认为自己
```

**使用 `request.target_user_id` 的 API**:
- `chat/views.py` — 消息读写（Message 查询/创建）、AI 回复（agent pipeline）
- `media/views.py` — get_media 附件下载（校验目标用户的消息附件权限）

**使用 `request.user_id`（登录用户）的 API**:
- 所有管理类 API（members CRUD、auth/me 等）
- 记忆管理 API（memory CRUD）
- 语音 API（voice sessions、声纹注册等，声纹在用户创建时由 MemberService 内部调用 Gateway 完成）
- 模型配置 API（models CRUD）
- 其他所有未列出的 API

**Alternatives considered**:
1. **中间件覆盖 request.user_id**: 影响所有 API（记忆、语音、媒体上传等都受切换影响），与用户意图不符 → 拒绝
2. **后端 Session 存储 target**: 增加后端复杂度，多标签页无法独立切换 → 拒绝
3. **每个 API 加 user_id 参数**: 侵入性极大，需修改 30+ 个视图 → 拒绝
4. **颁发新 Token**: 违反 spec "不颁发新令牌" 要求 → 拒绝

---

### R-002: 登录流程变更

**Decision**: 在 `AuthService.login()` 中增加 guest_expires_at 检查

**Rationale**:
- 过期访客不应能登录（`guest_expires_at < now` → 拒绝，提示"账号已过期"）
- 检查点在密码验证之后、Token 生成之前，避免为无效账号暴露密码信息
- 无需 is_deleted 检查：系统不提供删除功能，用户一旦创建不可删除

**Token Redis 数据扩展**:
```json
{
    "user_id": 1,
    "username": "anlin",
    "user_type": "admin",
    "member_type": "member",  // 新增
    "login_time": "2026-03-11T10:00:00+08:00"
}
```

---

### R-003: 前端 Target User ID 持久化与传播

**Decision**: localStorage + Zustand store + Axios 请求拦截器

**Rationale**:
- `localStorage.setItem("linchat_target_user_id", userId)` — 刷新保持
- `memberStore.targetUserId` — Zustand 响应式驱动 UI 刷新
- Axios 请求拦截器自动读取 store 并添加 `X-Target-User-Id` Header（仅当 target ≠ auth user 时）

**切换流程**:
```
点击用户 → memberStore.setTargetUser(userId, username)
  → localStorage 持久化
  → chatStore.clearMessages() + loadHistory()
  → 页面刷新目标用户的聊天数据
```

**影响范围**: 切换仅改变聊天界面展示的数据（消息历史 + AI 回复上下文），其他页面功能（记忆管理、语音设置等）不受影响，始终使用登录用户数据。

---

### R-004: 声纹注册集成到用户创建流程

**Decision**: 前端分步收集信息，一次性提交 `POST /api/v1/members/`（multipart/form-data），后端在单个请求中原子完成声纹注册（Gateway）和用户入库

**Rationale**:
- 用户创建是原子操作：要么声纹+用户同时成功入库（status=1），要么整体失败、数据库无残留
- 前端向导分步收集数据（步骤1 凭据、步骤2 录音），但不提前写数据库
- 后端 MemberService.create_member() 内部调用 Gateway 声纹注册 API，不通过 voice/views.py 端点
- 无需 X-Target-User-Id 参与声纹注册流程，voice API 不受影响

**流程**:
```
前端向导:
  Step 1: 选择 成员/访客 + 填写用户名密码（仅前端状态，不调 API）
  Step 2: 浏览器录音 10-30 秒（仅前端状态，不调 API）
  提交: POST /api/v1/members/ (multipart/form-data: username, password, member_type, audio)

后端 MemberService.create_member():
  1. 校验用户名唯一性
  2. 调用 Gateway 声纹注册 API（name=username, audio=音频文件）
  3. Gateway 失败 → 返回错误（VOICEPRINT_FAILED），数据库无写入
  4. Gateway 成功 → 事务内创建 SysUser(status=1) + SpeakerProfile(gateway_speaker_id)
  5. 返回完整用户信息
```

**中断恢复**: 向导中途退出（浏览器崩溃/关闭页面）时数据库无任何残留，用户可随时重新创建，用户名不被占用。

---

### R-005: 访客过期自动处理

**Decision**: Celery beat 每小时执行 `users.expire_guests` 任务

**Rationale**:
- 家庭场景 < 10 用户，每小时扫描一次开销极低
- 仅将 `status` 设为 0（禁用），过期访客不可恢复，如需使用须创建新账号
- 已登录的访客：Token 本身有 24h 绝对过期 + 1h 无操作过期，过期后无法继续使用

**任务模式**: 遵循 `memory/tasks.py` 模式 — `@shared_task(name="users.expire_guests")`，使用 `async_to_sync` 包裹异步调用

---

### R-006: WebSocket 语音连接的 Target User 处理

**Decision**: WebSocket 连接不支持 X-Target-User-Id 切换

**Rationale**:
- 语音模式是实时交互，用的是当前登录用户的声纹和设置
- 成员切换到其他用户视角后进入语音模式的场景不合理（声纹不匹配）
- 前端在进入语音模式前，如果当前处于切换视角，应先恢复为自己
- 系统中不存在无声纹的用户（初始管理员自带声纹 + 创建流程强制注册），无需声纹前置检查

---

### R-007: 前端 UI 组件设计

**Decision**: 底部输入框左侧添加圆形头像按钮 + 全屏模态框

**Rationale**:
- **头像按钮**: 显示当前操作用户的首字母（如 "D"），背景色从以下 8 色方案中按 `user_id % 8` 选取：`["#F87171", "#FB923C", "#FBBF24", "#34D399", "#60A5FA", "#818CF8", "#A78BFA", "#F472B6"]`（红/橙/黄/绿/蓝/靛/紫/粉），文字颜色统一白色
- **模态框**: 包含用户列表（可点击切换，过期访客灰色展示在底部）+ "添加用户"按钮 + 当前用户高亮标记
- **创建引导**: 分步 Wizard（Step 1: 类型+凭据, Step 2: 声纹录音）
- 仅 `member_type=member` 登录时显示按钮，`guest` 登录不显示

---

## Phase 1: Data Model

### SysUser 扩展字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `member_type` | CharField(20) | `"member"` | 业务类型：`member` (成员) / `guest` (访客) |
| `guest_expires_at` | DateTimeField | null | 访客有效期截止时间（仅 guest 使用） |

> **注意**: 不设 `is_deleted` 字段。用户一旦创建不可删除，该类型问题不予考虑。

**数据迁移策略**:
```
迁移 0005_add_multiuser_fields:
  1. AddField: member_type (default='member')
  2. AddField: guest_expires_at (null=True, blank=True)

注：无需存量数据迁移。新用户创建为原子操作（声纹注册+用户入库在同一请求中完成），
直接以 status=1 入库，不存在 status=0 的中间状态（status=0 仅用于过期访客）。
开发完成后全量清库，通过 reset_all_data management command 初始化带声纹的管理员测试账户。
```

**模型方法扩展**:
```python
def is_member(self) -> bool:
    return self.member_type == "member"

def is_guest_expired(self) -> bool:
    if self.member_type != "guest" or not self.guest_expires_at:
        return False
    return timezone.now() >= self.guest_expires_at
```

### 实体关系

```
SysUser (1) ──── (1) SpeakerProfile  # 所有用户均有声纹
    │
    ├── member_type: member/guest
    └── guest_expires_at: nullable datetime
```

无需新建模型。SpeakerProfile 已有 OneToOne 关联 SysUser。所有用户均有声纹（初始管理员自带声纹 + 创建为原子操作确保声纹与用户同时入库），无需展示声纹状态。

---

## Phase 1: API Contracts

详见 [contracts/members-api.md](contracts/members-api.md)（权威来源）。

**核心要点**:
- 新增 `/api/v1/members/` 端点：GET 列表 + POST 创建（仅 member 可访问，不提供 DELETE）
- `POST /api/v1/auth/login` 新增 ACCOUNT_EXPIRED 错误码（无 ACCOUNT_DELETED，系统不支持删除）
- `GET /api/v1/auth/me` 扩展返回 `member_type` 字段
- `X-Target-User-Id` Header 仅影响聊天/媒体 API（声纹注册在用户创建时由后端内部调用 Gateway，不通过 voice API 端点）
- 过期访客不可作为切换目标，前端禁止选择 + 中间件校验返回错误双重保障（宪法 1.4 显式失败）

---

## Phase 1: Quickstart

### 后端开发快速开始

```bash
# 1. 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 2. 数据库迁移（添加 SysUser 新字段）
python manage.py makemigrations users
python manage.py migrate

# 3. 运行测试
pytest tests/users/ -v

# 4. 启动服务验证
PYTHONUNBUFFERED=1 uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 前端开发快速开始

```bash
cd /home/dantsinghua/work/linchat/frontend

# 1. 添加新组件文件
mkdir -p src/components/members

# 2. 开发完成后构建
npm run build
npm run start -- -p 3784
```

### 关键实现路径

**Phase 1-2: 模型与基础设施** (对应 tasks.md Phase 1 Setup + Phase 2 Foundational)
1. SysUser 模型扩展 → 迁移（无需存量数据迁移，开发完成后全量清库）
2. TokenAuthMiddleware 扩展 X-Target-User-Id + 过期访客拦截
3. AuthService 登录流程扩展（过期检查）
4. MeView 返回 member_type
5. 前端 memberStore + memberService + Axios 拦截器
6. 聊天/媒体 API 适配 request.target_user_id（voice API 不参与，声纹注册在用户创建时由 MemberService 内部调用 Gateway）

**Phase 3-4: 成员管理与切换 — MVP** (对应 tasks.md Phase 3 US1 + Phase 4 US2)
1. MemberService（创建、列表）+ Members API 视图 + 路由
2. MemberSwitchModal（用户列表 + 切换）
3. MessageInput 左侧头像按钮 + ChatPage 集成

**Phase 5-6: 访客与声纹** (对应 tasks.md Phase 5 US3 + Phase 6 US4)
1. 访客过期 Celery 任务
2. VoiceprintRecorder 录音组件 + CreateMemberWizard 分步引导（前端收集全部数据后一次性提交 POST /members/）

**Phase 7: 语音模式统一** (对应 tasks.md Phase 7 US5)
1. 前端语音连接 mode 硬编码为 ambient + 清理 voice_chat 代码

---

*Plan generated by `/speckit.plan`. Next step: `/speckit.tasks` to generate task breakdown.*
