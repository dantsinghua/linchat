# Users 模块开发指南

> 本文件为 `apps/users` 用户认证模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

用户认证模块负责：验证码生成与校验、用户登录/登出、Token 鉴权、单点登录（SSO）冲突处理。

**不负责**：用户注册（当前通过管理命令创建）、权限管理、用户 CRUD。

---

## 目录结构

```
apps/users/
├── models.py          # SysUser 数据模型
├── views.py           # HTTP 视图（仅处理请求/响应）
├── services.py        # 业务逻辑（CaptchaService / AuthService）
├── repositories.py    # 数据访问层（ORM 操作，@sync_to_async）
├── serializers.py     # DRF 序列化器（请求验证）
├── crypto.py          # 加密工具（SM3/SM4/Token 生成）
├── urls.py            # 路由配置
├── apps.py            # Django App 配置
├── management/commands/
│   └── init_admin.py  # 初始化管理员命令
└── migrations/        # 数据库迁移
```

---

## 核心数据模型

### SysUser（表名：`sys_user`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | BigAutoField (PK) | 自增主键 |
| `username` | CharField(50, unique) | 用户名 |
| `password_hash` | CharField(255) | SM3 哈希密码 |
| `type` | CharField(20) | 用户类型：`admin` / `user` |
| `status` | SmallIntegerField | 0=禁用, 1=启用 |
| `login_fail_count` | IntegerField | 连续登录失败次数 |
| `lock_until` | DateTimeField | 账户锁定截止时间 |
| `message_count` | IntegerField | 累计消息数 |
| `total_tokens` | BigIntegerField | 累计 Token 消耗数 |
| `last_active_time` | DateTimeField | 最后活跃时间 |
| `last_login_time` | DateTimeField | 最后登录时间 |
| `last_login_ip` | CharField(50) | 最后登录 IP |
| `created_time` / `updated_time` | DateTimeField(auto) | 时间戳 |

**模型方法**：`is_locked()` / `is_active()` / `is_admin()`

---

## API 端点

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/v1/auth/captcha` | 否 | 生成验证码图片 |
| POST | `/api/v1/auth/login` | 否 | 用户登录 |
| POST | `/api/v1/auth/logout` | 是 | 用户登出 |
| GET | `/api/v1/auth/me` | 是 | 获取当前用户信息 |

---

## 认证流程核心逻辑

### 登录流程

```
验证码校验 → 查询用户 → 检查锁定 → SM4 解密密码 → SM3 比对哈希
→ 生成 Token（SM4 加密） → SSO 冲突处理（踢旧会话）
→ Token Hash 存入 Redis → 设置 httpOnly Cookie → 返回用户信息
```

### Token 双重过期机制

- **无操作过期**：1 小时无请求自动过期（每次请求刷新 TTL）
- **绝对过期**：登录后 24 小时强制失效（不可刷新）

### SSO 冲突处理

新登录时检查旧 Token，若存在则：删除旧 Token → 通过 Redis Pub/Sub 发送 `SSO_CONFLICT` 登出事件 → 前端旧会话收到 SSE 通知后跳转登录页。

### 登录失败保护

- 连续 5 次密码错误 → 锁定账户 15 分钟
- 用户不存在时也在 Redis 增加计数（防枚举攻击）

---

## Redis 键设计

| 键名模式 | TTL | 用途 |
|----------|-----|------|
| `auth:captcha:{captcha_id}` | 120s | 验证码文本（一次性） |
| `auth:token:{token_hash}` | 3600s | Token 信息（JSON） |
| `auth:user_token:{user_id}` | 86400s | 用户当前 Token Hash 索引 |
| `auth:fail:{username}` | 900s | 登录失败计数 |
| `events:user:{user_id}` | - | SSE 事件发布频道（Pub/Sub） |

---

## 加密体系

| 算法 | 用途 | 函数 |
|------|------|------|
| SM3 | 密码哈希存储 | `sm3_hash()` / `verify_password()` |
| SM4 (ECB) | 前端密码传输加密、Token 内容加密 | `sm4_encrypt()` / `sm4_decrypt()` |
| SHA256 | Token Hash（Redis 键名） | `generate_token_hash()` |

**密码比对必须使用 `secrets.compare_digest`（常量时间，防时序攻击）。**

---

## 配置参数

```python
AUTH_TOKEN_IDLE_TTL = 3600       # Token 无操作过期：1 小时
AUTH_TOKEN_ABSOLUTE_TTL = 86400  # Token 绝对过期：24 小时
AUTH_CAPTCHA_TTL = 120           # 验证码有效期：2 分钟
AUTH_MAX_FAIL_COUNT = 5          # 最大失败次数
AUTH_LOCK_DURATION = 900         # 账户锁定：15 分钟
AUTH_FAIL_COUNT_TTL = 900        # 失败计数过期：15 分钟
```

---

## 异常体系

所有认证异常继承自 `AuthException`，定义在 `apps/common/exceptions.py`：

| 异常类 | HTTP 状态码 | 场景 |
|--------|------------|------|
| `AuthFailedException` | 400 | 用户名或密码错误 |
| `CaptchaInvalidException` | 400 | 验证码错误/过期 |
| `TokenExpiredException` | 401 | Token 已过期 |
| `AccountLockedException` | 403 | 账户被锁定（含 `remaining_seconds`） |
| `UserDisabledException` | 403 | 账户已禁用 |

---

## 开发规范

### 分层职责（严格遵守）

- **views.py**：仅做 HTTP 解析和响应包装，禁止业务逻辑
- **services.py**：封装所有业务逻辑，是核心层
- **repositories.py**：封装 ORM 操作，所有方法必须用 `@sync_to_async` 装饰
- **crypto.py**：纯加密工具函数，无状态

### 异步优先

- 所有视图必须是 `async def`（配合 uvicorn ASGI）
- 数据库操作通过 `@sync_to_async` 适配
- Redis 操作使用异步客户端（`core/redis.py`）
- **禁止**在异步视图中使用同步阻塞调用

### 安全红线

- Token **只能**存储在 httpOnly Cookie，禁止 localStorage
- 密码比对**必须**使用常量时间比较
- Token 原文**禁止**持久化，只存 SHA256 Hash
- SM4 密钥从 `settings.SM4_SECRET_KEY` 获取，禁止硬编码

### 编码风格

- 类型注解：所有公共函数必须添加完整类型声明
- 文档字符串：Google 风格（参数、返回值、异常）
- 日志：关键操作使用 `logging` 记录（登录成功/失败、Token 操作）
- 中文注释：模块和关键逻辑用中文注释

---

## 关键依赖

| 依赖 | 位置 | 说明 |
|------|------|------|
| Token 中间件 | `apps/common/middleware.py` | `TokenAuthMiddleware` / `AsyncTokenAuthMiddleware` |
| 异常处理器 | `apps/common/exceptions.py` | 全局异常定义与 DRF 异常处理 |
| Redis 工具 | `core/redis.py` | 异步 Redis 客户端封装 |
| 响应格式 | `apps/common/response.py` | 统一 API 响应格式 |

---

## 管理命令

```bash
# 初始化管理员账户（密码：!9871229Qing）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py init_admin
```
