# Users 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 用户认证模块：验证码、登录/登出、Token 鉴权、SSO 冲突处理。

## 文件清单

| 文件 | 职责 |
|------|------|
| `models.py` | SysUser 数据模型（表 `sys_user`） |
| `views.py` | HTTP 视图：CaptchaView / LoginView / LogoutView / MeView |
| `services.py` | 业务逻辑：CaptchaService / AuthService |
| `repositories.py` | 数据访问层（ORM + @sync_to_async） |
| `serializers.py` | LoginRequestSerializer |
| `crypto.py` | 国密工具：SM3 哈希、SM4 加解密、Token 生成 |
| `urls.py` | 路由配置 |
| `management/commands/init_admin.py` | 初始化管理员命令 |

## 核心模型 SysUser（表 `sys_user`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | BigAutoField (PK) | 主键 |
| `username` | CharField(50, unique) | 用户名 |
| `password_hash` | CharField(255) | SM3 哈希 |
| `type` | CharField(20) | `admin` / `user` |
| `status` | SmallIntegerField | 0=禁用, 1=启用 |
| `login_fail_count` / `lock_until` | 失败次数 / 锁定截止 | |
| `message_count` / `total_tokens` | 消息计数 / Token 消耗 | |
| `last_active_time` / `last_login_time` / `last_login_ip` | 活跃/登录信息 | |

方法: `is_locked()` / `is_active()` / `is_admin()`

## API 端点

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/v1/auth/captcha` | 否 | 生成验证码图片 |
| POST | `/api/v1/auth/login` | 否 | 登录 |
| POST | `/api/v1/auth/logout` | 是 | 登出 |
| GET | `/api/v1/auth/me` | 是 | 当前用户信息 |

## 认证流程

```
验证码校验 -> 查询用户 -> 检查锁定 -> SM4 解密 -> SM3 比对
-> 生成 Token -> SSO 冲突（踢旧会话）-> Redis 存储 -> httpOnly Cookie
```

- Token 无操作过期: 1 小时（请求刷新 TTL）；绝对过期: 24 小时
- 连续 5 次密码错误 -> 锁定 15 分钟

## 加密体系 (crypto.py)

| 算法 | 用途 | 函数 |
|------|------|------|
| SM3 | 密码哈希 | `sm3_hash()` / `verify_password()` |
| SM4 (ECB) | 密码传输 + Token 加密 | `sm4_encrypt()` / `sm4_decrypt()` |
| SHA256 | Token Hash（Redis 键名） | `generate_token_hash()` |

密码比对使用 `secrets.compare_digest`（防时序攻击）。

## Redis 键

| 键模式 | TTL | 用途 |
|--------|-----|------|
| `auth:captcha:{id}` | 120s | 验证码文本 |
| `auth:token:{hash}` | 3600s | Token 信息 JSON |
| `auth:user_token:{uid}` | 86400s | 用户当前 Token Hash |
| `auth:fail:{username}` | 900s | 登录失败计数 |

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.common.middleware` | TokenAuthMiddleware + Cookie 工具 |
| `apps.common.exceptions` | AuthException 体系 |
| `apps.common.event_service` | SSO 冲突 Pub/Sub 事件 |
| `core.redis` | 异步 Redis 客户端 |
| gmssl | 国密 SM3/SM4 |
| captcha | 验证码图片生成 |

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/users/ -v
```
