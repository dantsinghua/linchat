# Users 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> 用户认证模块：验证码、登录/登出、Token 鉴权、SSO 冲突处理、家庭成员管理（015-family-multiuser）。

## 文件清单

| 文件 | 职责 |
|------|------|
| `models.py` | SysUser 数据模型（表 `sys_user`） |
| `views.py` | HTTP 视图：CaptchaView / LoginView / LogoutView / MeView / MemberListCreateView |
| `services.py` | 业务逻辑：CaptchaService / AuthService / MemberService |
| `repositories.py` | 数据访问层（ORM + @sync_to_async），含 `list_members()` 成员列表查询 |
| `serializers.py` | LoginRequestSerializer / CreateMemberSerializer / MemberListSerializer |
| `crypto.py` | 国密工具：SM3 哈希、SM4 加解密、Token 生成 |
| `exceptions.py` | 自定义异常：UsernameExistsError / VoiceprintRegistrationError |
| `tasks.py` | Celery 定时任务：`expire_guests`（扫描过期访客设 status=0） |
| `urls.py` | 路由配置（auth + members） |
| `management/commands/init_admin.py` | 初始化管理员命令 |
| `management/commands/reset_all_data.py` | 全量清库 + 管理员重建命令（015 新增） |

## 核心模型 SysUser（表 `sys_user`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | BigAutoField (PK) | 主键 |
| `username` | CharField(50, unique) | 用户名 |
| `password_hash` | CharField(255) | SM3 哈希 |
| `type` | CharField(20) | `admin` / `user` |
| `member_type` | CharField(20) | `member`（成员）/ `guest`（访客）— 015 新增 |
| `guest_expires_at` | DateTimeField (nullable) | 访客有效期截止时间 — 015 新增 |
| `status` | SmallIntegerField | 0=禁用, 1=启用 |
| `login_fail_count` / `lock_until` | 失败次数 / 锁定截止 | |
| `message_count` / `total_tokens` | 消息计数 / Token 消耗 | |
| `last_active_time` / `last_login_time` / `last_login_ip` | 活跃/登录信息 | |

方法: `is_locked()` / `is_active()` / `is_admin()` / `is_member()` / `is_guest_expired()`

## API 端点

| 方法 | 路径 | 认证 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/api/v1/auth/captcha` | 否 | — | 生成验证码图片 |
| POST | `/api/v1/auth/login` | 否 | — | 登录 |
| POST | `/api/v1/auth/logout` | 是 | — | 登出 |
| GET | `/api/v1/auth/me` | 是 | — | 当前用户信息（含 member_type） |
| GET | `/api/v1/members/` | 是 | member_type=member | 家庭成员列表（015 新增） |
| POST | `/api/v1/members/` | 是 | member_type=member | 创建家庭成员（MultiPart: username + password + member_type + audio）（015 新增） |

## 服务层

### CaptchaService
- `generate()` — 生成验证码图片 + Redis 缓存
- `verify()` — 校验验证码

### AuthService
- `login()` — 完整登录流程（验证码→密码→Token→SSO）
- `logout()` — 清除 Token

### MemberService（015 新增）
- `list_members(include_expired)` — 获取家庭成员列表，支持过滤已过期访客
- `create_member(username, password_encrypted, member_type, audio_file, created_by_user_id)` — 原子创建成员：校验用户名 → Gateway 声纹注册 → 事务内创建 SysUser + SpeakerProfile

## 自定义异常（015 新增）

| 异常 | 错误码 | 说明 |
|------|--------|------|
| `UsernameExistsError` | USERNAME_EXISTS | 用户名已存在 |
| `VoiceprintRegistrationError` | VOICEPRINT_FAILED | Gateway 声纹注册失败 |

## Celery 定时任务（015 新增）

| 任务名 | 调度 | 说明 |
|--------|------|------|
| `users.expire_guests` | 定时扫描 | 将过期访客（guest_expires_at <= now）设 status=0 |

## Management Commands

| 命令 | 说明 |
|------|------|
| `init_admin` | 初始化 admin 用户（固定密码） |
| `reset_all_data` | 全量清库 + 管理员 anlin 重建（015 新增） |

### reset_all_data 用法

```bash
# 交互确认
python manage.py reset_all_data --password <明文密码> --audio <音频路径>

# 跳过确认
python manage.py reset_all_data --password <明文密码> --audio <音频路径> --yes
```

功能:
1. 清空 PostgreSQL 业务表（按外键顺序）
2. 清空 MinIO 存储桶（linchat-media, linchat-thumbnails）
3. Gateway 声纹注册 + 创建管理员 SysUser（anlin, type=admin, member_type=member）+ SpeakerProfile

## 认证流程

```
验证码校验 -> 查询用户 -> 检查锁定 -> 检查访客过期 -> SM4 解密 -> SM3 比对
-> 生成 Token（含 member_type）-> SSO 冲突（踢旧会话）-> Redis 存储 -> httpOnly Cookie
```

- Token 无操作过期: 1 小时（请求刷新 TTL）；绝对过期: 24 小时
- 连续 5 次密码错误 -> 锁定 15 分钟
- 过期访客登录拒绝

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
| `auth:token:{hash}` | 3600s | Token 信息 JSON（含 member_type） |
| `auth:user_token:{uid}` | 86400s | 用户当前 Token Hash |
| `auth:fail:{username}` | 900s | 登录失败计数 |

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.common.middleware` | TokenAuthMiddleware + Cookie 工具 |
| `apps.common.exceptions` | AuthException 体系 + BusinessException |
| `apps.common.event_service` | SSO 冲突 Pub/Sub 事件 |
| `apps.common.gateway_utils` | Gateway URL/Headers（MemberService 声纹注册） |
| `apps.voice.models` | SpeakerProfile（MemberService 创建关联） |
| `core.redis` | 异步 Redis 客户端 |
| gmssl | 国密 SM3/SM4 |
| captcha | 验证码图片生成 |
| httpx | Gateway HTTP 调用（MemberService + reset_all_data） |

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/users/ -v
```


<claude-mem-context>

</claude-mem-context>