# Research: 家庭多用户系统

**Feature**: 015-family-multiuser | **Date**: 2026-03-11

---

## R-001: Target User ID 后端传播机制

**Decision**: `X-Target-User-Id` HTTP Header + TokenAuthMiddleware 设置独立的 `request.target_user_id`（**不覆盖** `request.user_id`）

**Rationale**:
- `request.user_id` 始终为登录用户，权限校验、记忆管理等基于登录身份不变
- 新增 `request.target_user_id` 仅供聊天/媒体 API 显式读取（声纹注册在用户创建时由 MemberService 内部调用 Gateway 完成，不通过 voice API 端点）
- 仅 `member_type=member` 可使用此 Header，访客请求忽略此 Header
- 无需 `request.auth_user_id`，`request.user_id` 本身就是登录用户

**Alternatives considered**:
| 方案 | 拒绝原因 |
|------|----------|
| 中间件覆盖 request.user_id | 影响所有 API（记忆、语音等都受切换影响），与用户意图不符 |
| 后端 Session 存储 target user | 多标签页无法独立切换；增加后端复杂度 |
| 每个 API 加 user_id 参数 | 侵入性极大，需修改 30+ 个视图 |
| 颁发新 Token 模拟登录 | 违反 spec "不颁发新令牌" 要求 |
| URL 参数 ?target_user_id= | 暴露 user_id 于 URL，安全隐患 |

**Implementation impact**:
- `backend/apps/common/middleware.py` — TokenAuthMiddleware 扩展（~20 行）
- `backend/apps/common/websocket_auth.py` — WebSocket 不支持 X-Target-User-Id（语音模式用真实身份）
- `frontend/src/services/api.ts` — Axios 请求拦截器添加 Header（~5 行）

---

## R-002: 登录流程变更

**Decision**: 在 AuthService.login() 密码验证之后、Token 生成之前增加过期检查

**Rationale**:
- `member_type='guest' AND guest_expires_at < now` → AuthFailedException("账号已过期")
- 检查在密码验证后，Token 生成前
- 无需 is_deleted 检查：系统不提供删除功能，用户一旦创建不可删除

**Token Redis 数据扩展**:
```json
{
    "user_id": 1,
    "username": "anlin",
    "user_type": "admin",
    "member_type": "member",
    "login_time": "2026-03-11T10:00:00+08:00"
}
```

中间件 `_verify_token_sync()` 返回的 `token_info` 自然包含 `member_type`，无需额外查询数据库。

---

## R-003: 前端 Target User ID 持久化策略

**Decision**: localStorage + Zustand memberStore + Axios 请求拦截器

**Rationale**:
- `localStorage.setItem("linchat_target_user_id", userId)` — 刷新保持
- `memberStore.targetUserId` / `memberStore.targetUsername` — Zustand 驱动 UI 刷新
- `memberStore.isViewingOther` — computed：`targetUserId !== authUserId`
- Axios 拦截器：仅当 `isViewingOther` 时添加 `X-Target-User-Id` Header

**切换流程**:
```
1. 用户点击模态框中的目标用户
2. memberStore.setTargetUser(userId, username)
3. localStorage 同步写入
4. chatStore.clearMessages()
5. chatStore.loadHistory() — 自动携带 X-Target-User-Id
6. 模态框关闭，输入框头像更新为目标用户
```

**恢复自身**:
```
1. 用户点击"回到自己" / 再次点击自己
2. memberStore.clearTarget()
3. localStorage 移除 linchat_target_user_id
4. chatStore.clearMessages() + loadHistory()
```

---

## R-004: 声纹注册集成到用户创建

**Decision**: 前端分步收集全部信息，一次性提交 `POST /api/v1/members/`（multipart/form-data），后端 MemberService 内部调用 Gateway 声纹注册 API 完成原子创建

**Rationale**:
- 用户创建为原子操作：声纹注册+用户入库在同一请求中完成，任一失败则整体失败、数据库无残留
- 后端 MemberService.create_member() 接收 audio 文件，内部调用 Gateway 声纹注册 API
- 不通过 voice/views.py 端点，不需要 X-Target-User-Id 参与声纹注册
- 避免了中间 status=0 状态和中间件校验冲突

**替代方案（已拒绝）**:
- 前端分两步 API 调用（Step 1 创建 status=0 用户 → Step 2 X-Target-User-Id 声纹注册）→ 中间件 status=1 校验与 status=0 新用户冲突，形成死锁 → 拒绝
- 声纹 API 增加 `for_user_id` 参数 → 需要修改 voice 模块代码和测试 → 拒绝

---

## R-005: 访客过期自动处理

**Decision**: Celery beat 每小时执行 `users.expire_guests`

**Rationale**:
- SC-004 要求"过期后 1 小时内自动失效"，每小时检查即满足
- 仅设置 `status=0`（禁用）
- 过期用户已登录的 Token 仍有效直到自然过期（最长 24h），但 spec 允许这种行为

**实现模式**: 复用 `memory/tasks.py` 的 `@shared_task` 模式

```python
@shared_task(name="users.expire_guests")
def expire_guests() -> None:
    from apps.users.models import SysUser
    from django.utils import timezone
    expired = SysUser.objects.filter(
        member_type="guest",
        guest_expires_at__lte=timezone.now(),
        status=1,
    )
    count = expired.update(status=0)
    if count:
        logger.info("Expired %d guest accounts", count)
```

---

## R-006: WebSocket 语音模式与 Target User

**Decision**: WebSocket 连接始终使用真实登录身份，不支持 X-Target-User-Id

**Rationale**:
- 语音模式需要声纹匹配，必须用真实用户的声纹
- 前端在切换视角后进入语音模式时，应先恢复为自身
- WebSocket TokenAuthMiddleware 不读取 X-Target-User-Id Header

---

## R-007: 成员管理 UI 方案

**Decision**: 底部输入框左侧圆形头像按钮 + 全屏模态框

**UI 结构**:
```
MessageInput 底部工具栏:
[头像按钮 "D"] [附件] [语音录制] [语音模式]     [发送]

MemberSwitchModal:
┌─────────────────────────────────────────┐
│                                    [✕]  │
│  家庭成员                               │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ (D) anlin     成员             │ ← 当前用户高亮 │
│  ├─────────────────────────────────┤   │
│  │ (Z) zhenghui  成员             │   │
│  ├─────────────────────────────────┤   │
│  │ (X) xiaoming  访客             │   │
│  └─────────────────────────────────┘   │
│                                         │
│  [+ 添加用户]                           │
└─────────────────────────────────────────┘
```

**头像颜色方案**: 预设 8 种背景色，按 `user_id % 8` 选取
```
['#3B82F6', '#10B981', '#F59E0B', '#EF4444',
 '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16']
```
