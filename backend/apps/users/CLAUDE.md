# Users 模块开发指南

> `apps/users` 用户认证模块，负责验证码、登录/登出、Token 鉴权、单点登录（SSO）。

---

## 模块职责

- 验证码生成与校验
- 用户登录/登出
- Token 鉴权（双重过期机制）
- 单点登录（SSO）冲突处理
- 国密算法加密工具（SM3/SM4）

**不负责**: 用户注册（通过 `init_admin` 管理命令创建）、权限管理、用户 CRUD。

---

## 目录结构

```
apps/users/
├── models.py              # SysUser 数据模型
├── views.py               # HTTP 视图（异步，仅处理请求/响应）
├── services.py            # 业务逻辑（CaptchaService / AuthService）
├── repositories.py        # 数据访问层（ORM 操作，@sync_to_async）
├── serializers.py         # DRF 序列化器（LoginRequestSerializer）
├── crypto.py              # 加密工具（SM3/SM4/Token 生成）
├── urls.py                # 路由配置
├── apps.py                # Django App 配置
├── __init__.py
├── management/commands/
│   └── init_admin.py      # 初始化管理员命令
└── migrations/            # 数据库迁移
```

---

## 核心数据模型

### SysUser（表名: `sys_user`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | BigAutoField (PK) | 自增主键 |
| `username` | CharField(50, unique) | 用户名 |
| `password_hash` | CharField(255) | SM3 哈希密码 |
| `type` | CharField(20) | 用户类型: `admin` / `user` |
| `status` | SmallIntegerField | 0=禁用, 1=启用 |
| `login_fail_count` | IntegerField | 连续登录失败次数 |
| `lock_until` | DateTimeField (nullable) | 账户锁定截止时间 |
| `message_count` | IntegerField | 累计消息数 |
| `total_tokens` | BigIntegerField | 累计 Token 消耗数 |
| `last_active_time` | DateTimeField (nullable) | 最后活跃时间 |
| `last_login_time` | DateTimeField (nullable) | 最后登录时间 |
| `last_login_ip` | CharField(50, nullable) | 最后登录 IP |
| `created_time` / `updated_time` | DateTimeField(auto) | 时间戳 |

**模型方法**: `is_locked()` / `is_active()` / `is_admin()`

---

## API 端点

| 方法 | 路径 | 认证 | 视图类 | 说明 |
|------|------|------|--------|------|
| GET | `/api/v1/auth/captcha` | 否 | `CaptchaView` | 生成验证码图片（Base64） |
| POST | `/api/v1/auth/login` | 否 | `LoginView` | 用户登录 |
| POST | `/api/v1/auth/logout` | 是 | `LogoutView` | 用户登出 |
| GET | `/api/v1/auth/me` | 是 | `MeView` | 获取当前用户信息 |

---

## 认证流程

### 登录流程

```
验证码校验 -> 查询用户 -> 检查锁定 -> SM4 解密密码 -> SM3 比对哈希
-> 生成 Token（SM4 加密） -> SSO 冲突处理（踢旧会话）
-> Token Hash 存入 Redis -> 设置 httpOnly Cookie -> 返回用户信息
```

### Token 双重过期机制

- **无操作过期**: 1 小时无请求自动过期（每次请求中间件刷新 TTL）
- **绝对过期**: 登录后 24 小时强制失效（不可刷新）

### SSO 冲突处理

新登录时检查旧 Token，若存在则: 删除旧 Token -> 通过 Redis Pub/Sub 发送 `SSO_CONFLICT` 登出事件 -> 前端旧会话收到 SSE 通知后跳转登录页。

### 登录失败保护

- 连续 5 次密码错误 -> 锁定账户 15 分钟
- 用户不存在时也在 Redis 增加计数（防枚举攻击）

---

## Redis 键设计

| 键名模式 | TTL | 用途 |
|----------|-----|------|
| `auth:captcha:{captcha_id}` | 120s | 验证码文本（一次性使用） |
| `auth:token:{token_hash}` | 3600s | Token 信息（JSON: user_id, username, user_type, login_time 等） |
| `auth:user_token:{user_id}` | 86400s | 用户当前 Token Hash 索引（用于 SSO） |
| `auth:fail:{username}` | 900s | 登录失败计数 |

---

## 加密体系 (crypto.py)

| 算法 | 用途 | 函数 |
|------|------|------|
| SM3 | 密码哈希存储 | `sm3_hash()` / `verify_password()` |
| SM4 (ECB) | 前端密码传输加密、Token 内容加密 | `sm4_encrypt()` / `sm4_decrypt()` / `sm4_decrypt_safe()` |
| SHA256 | Token Hash（Redis 键名） | `generate_token_hash()` |

**密码比对使用 `secrets.compare_digest`（常量时间，防时序攻击）。**

Token 格式: `SM4_Encrypt("{username}|{password}|{captcha_code}|{timestamp}")`

---

## 仓库层 (repositories.py)

`UserRepository` 封装所有 `SysUser` ORM 操作，所有方法使用 `@sync_to_async` 装饰:

| 方法 | 说明 |
|------|------|
| `find_by_id(user_id)` | 按 ID 查找用户 |
| `find_by_username(username)` | 按用户名查找用户 |
| `create(username, password_hash)` | 创建用户 |
| `save(user)` | 保存用户 |
| `update_login_info(user, login_time, login_ip)` | 更新登录信息、重置失败计数 |
| `increment_fail_count(user, lock_until)` | 递增失败计数、达上限锁定 |
| `add_message_count(user_id, count)` | 增加消息计数 |
| `add_tokens(user_id, tokens)` | 增加 Token 消耗统计 |

全局实例: `user_repo = UserRepository()`

---

## 序列化器 (serializers.py)

`LoginRequestSerializer`: 验证登录请求字段（username, password, captcha_id, captcha_code），含非空和长度校验。

---

## 配置参数

```python
AUTH_TOKEN_IDLE_TTL = 3600       # Token 无操作过期: 1 小时
AUTH_TOKEN_ABSOLUTE_TTL = 86400  # Token 绝对过期: 24 小时
AUTH_CAPTCHA_TTL = 120           # 验证码有效期: 2 分钟
AUTH_MAX_FAIL_COUNT = 5          # 最大失败次数
AUTH_LOCK_DURATION = 900         # 账户锁定: 15 分钟
AUTH_FAIL_COUNT_TTL = 900        # 失败计数过期: 15 分钟
```

---

## 异常体系

所有认证异常继承自 `AuthException`，定义在 `apps/common/exceptions.py`:

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

- **views.py**: 仅做 HTTP 解析和响应包装，禁止业务逻辑
- **services.py**: 封装所有业务逻辑（CaptchaService / AuthService）
- **repositories.py**: 封装 ORM 操作，所有方法必须用 `@sync_to_async`
- **crypto.py**: 纯加密工具函数，无状态

### 安全红线

- Token **只能**存储在 httpOnly Cookie，禁止 localStorage
- 密码比对**必须**使用常量时间比较
- Token 原文**禁止**持久化，只存 SHA256 Hash
- SM4 密钥从 `settings.SM4_SECRET_KEY` 获取，禁止硬编码

---

## 关键依赖

| 依赖 | 位置 | 说明 |
|------|------|------|
| Token 中间件 | `apps/common/middleware.py` | `TokenAuthMiddleware`（同步，Django 自动线程池适配异步视图） |
| 异常处理器 | `apps/common/exceptions.py` | 全局异常定义与 DRF 异常处理 |
| Redis 工具 | `core/redis.py` | 异步 Redis 客户端封装 + 键名工具 |
| 响应格式 | `apps/common/responses.py` | 统一 API 响应格式 |
| 事件服务 | `apps/common/event_service.py` | SSO 冲突时发送 Pub/Sub 登出事件 |

---

## 管理命令

```bash
# 初始化管理员账户
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py init_admin
```
