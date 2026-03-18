# API Contract: Members Management

**Feature**: 015-family-multiuser | **Version**: 1.0
**Base Path**: `/api/v1/members/`
**Authentication**: Required (httpOnly Cookie Token)
**Authorization**: `member_type=member` only (guests receive 403)

---

## Endpoints

### GET /api/v1/members/

**Description**: 列出所有用户，含过期状态

**Authorization**: member only

**Query Parameters**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| include_expired | boolean | N | 默认 false；为 true 时返回过期访客（附 is_expired=true 标记） |

**Response 200**:
```json
{
    "code": "SUCCESS",
    "data": [
        {
            "user_id": 1,
            "username": "anlin",
            "member_type": "member",
            "status": 1,
            "guest_expires_at": null,
            "is_expired": false,
            "created_time": "2026-01-01T00:00:00+08:00"
        }
    ],
    "message": "操作成功"
}
```

**Response 403** (guest calling):
```json
{
    "code": "FORBIDDEN",
    "data": null,
    "message": "权限不足"
}
```

---

### POST /api/v1/members/

**Description**: 创建新家庭成员或访客

**Authorization**: member only

**Content-Type**: `multipart/form-data`（含音频文件）

**Request Fields**:

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| username | string | Y | 3-50 字符，仅字母数字下划线，唯一 |
| password | string | Y | SM4 加密密码，解密后 6-50 个 ASCII 可打印字符（0x20-0x7E，不允许中文及其他非 ASCII） |
| member_type | string | Y | `"member"` 或 `"guest"` |
| audio | file | Y | 声纹录音文件（10-30 秒），复用 Gateway 声纹注册 API 所接受的格式 |

> **设计决策**：前端分步向导收集全部信息（步骤1 凭据、步骤2 录音），完成后一次性提交。后端原子完成声纹注册（Gateway API）和用户入库（status=1），任一环节失败则整体失败、数据库无残留。家庭场景 <10 用户，不实现分页。

**Response 201**:
```json
{
    "code": "SUCCESS",
    "data": {
        "user_id": 5,
        "username": "xiaoming",
        "member_type": "guest",
        "status": 1,
        "guest_expires_at": "2026-03-18T10:00:00+08:00"
    },
    "message": "用户创建成功"
}
```

**Error Responses**:
| HTTP | Code | Condition |
|------|------|-----------|
| 400 | VALIDATION_ERROR | 参数校验失败 |
| 400 | USERNAME_EXISTS | 用户名已存在 |
| 400 | VOICEPRINT_FAILED | 声纹注册失败（Gateway 返回错误，如音质不足） |
| 403 | FORBIDDEN | 访客调用 |

---

> **注意**: 系统不提供 DELETE 端点。用户一旦创建不可删除，该类型问题不予考虑。

---

## Login Changes

### POST /api/v1/auth/login (变更)

**Description**: 登录流程新增过期访客检查（密码验证后、Token 生成前）

**新增错误响应**:
| HTTP | Code | Condition |
|------|------|-----------|
| 400 | ACCOUNT_EXPIRED | 访客账号已过期（guest_expires_at < now） |

> **设计决策**：使用 400 而非 401，因为密码验证已通过（身份认证成功），拒绝原因是业务状态（账号过期），属于业务逻辑错误而非认证失败。无需 ACCOUNT_DELETED 错误码（系统不支持删除）。

---

## Extended Endpoints

### GET /api/v1/auth/me (扩展)

**Description**: 返回当前登录用户信息，新增 member_type 字段

**Response 200**:
```json
{
    "code": "SUCCESS",
    "data": {
        "user_id": 1,
        "username": "anlin",
        "type": "admin",
        "member_type": "member"
    }
}
```

---

## Custom Header: X-Target-User-Id

**Scope**: 仅聊天相关 API 显式使用（中间件对所有请求解析，但仅以下 API 读取 `request.target_user_id`）

**使用 `request.target_user_id` 的 API**:
- `chat/views.py` — 消息读写（Message 查询/创建）、AI 回复（agent pipeline 含记忆召回）
- `media/views.py` — get_media 附件下载（校验目标用户消息附件权限）

> **注意**: 声纹注册在用户创建时由后端 MemberService 内部调用 Gateway API 完成，不通过 voice/views.py 端点。

**Behavior**:
1. 仅 `member_type=member` 的已认证用户可使用
2. 目标用户必须存在且未过期（过期访客不允许切换，前端禁止选择）
3. 生效时设置 `request.target_user_id` 为目标值（`request.user_id` 始终为登录用户不覆盖）
4. 无此 Header 或访客调用时：`request.target_user_id = request.user_id`（默认为自己）
5. 不需要 `auth_user_id` 概念 — `request.user_id` 本身就是登录用户

**Validation errors**: 目标用户不存在或已过期时，返回 400 错误（错误码 `TARGET_USER_INVALID`，message 包含具体原因）。前端收到错误后自动清除 targetUserId 并恢复自身视角（宪法 1.4 显式失败原则）。

---

## Permission Matrix

| Endpoint | member | guest |
|----------|--------|-------|
| GET /members/ | ✅ | ❌ 403 |
| POST /members/ | ✅ | ❌ 403 |
| X-Target-User-Id header | ✅ 生效 | ❌ 忽略 |
| GET /auth/me | ✅ | ✅ |
| Chat APIs (消息/AI回复) | ✅ (支持切换 target_user_id) | ✅ (仅自己) |
| Media GET (消息附件下载) | ✅ (支持切换 target_user_id) | ✅ (仅自己) |
| Voice POST speakers (声纹注册) | ✅ (仅自己) | ✅ (仅自己) |
| Memory/Voice 其他/Models APIs | ✅ (始终登录用户 user_id) | ✅ (仅自己) |
