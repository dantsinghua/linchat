# Data Model: 家庭多用户系统

**Feature**: 015-family-multiuser | **Date**: 2026-03-11

---

## Entity: SysUser (扩展)

**Table**: `sys_user`
**Status**: 扩展现有模型，新增 2 个字段（member_type、guest_expires_at）

### 完整字段

| 字段 | 类型 | 默认值 | 约束 | 新增 | 说明 |
|------|------|--------|------|------|------|
| `user_id` | BigAutoField | auto | PK | — | 自增主键 |
| `username` | CharField(50) | — | unique | — | 用户名（=显示名，头像首字母取首字符大写） |
| `password_hash` | CharField(255) | — | — | — | SM3 哈希密码 |
| `type` | CharField(20) | `"user"` | — | — | legacy 字段：admin/user（保留，不用于 015 权限判断） |
| `status` | SmallIntegerField | 1 | — | — | 0=禁用, 1=启用 |
| `member_type` | CharField(20) | `"member"` | — | **新增** | 业务类型：`member` (成员) / `guest` (访客) |
| `guest_expires_at` | DateTimeField | null | nullable | **新增** | 访客有效期截止时间 |
| `login_fail_count` | IntegerField | 0 | — | — | 登录失败计数 |
| `lock_until` | DateTimeField | null | nullable | — | 锁定截止时间 |
| `message_count` | IntegerField | 0 | — | — | 消息数量 |
| `total_tokens` | BigIntegerField | 0 | — | — | Token 消耗 |
| `last_active_time` | DateTimeField | null | nullable | — | 最后活跃时间 |
| `last_login_time` | DateTimeField | null | nullable | — | 最后登录时间 |
| `last_login_ip` | CharField(50) | null | nullable | — | 最后登录 IP |
| `created_time` | DateTimeField | auto | auto_now_add | — | 创建时间 |
| `updated_time` | DateTimeField | auto | auto_now | — | 更新时间 |

### 新增方法

```python
def is_member(self) -> bool:
    """是否为家庭成员"""
    return self.member_type == "member"

def is_guest_expired(self) -> bool:
    """访客是否已过期"""
    if self.member_type != "guest" or not self.guest_expires_at:
        return False
    return timezone.now() >= self.guest_expires_at
```

> **注意**: 系统不提供 `is_deleted` 字段和删除功能。用户一旦创建不可删除，该类型问题不予考虑。

### 验证规则

| 规则 | 说明 |
|------|------|
| username 唯一 | 数据库 UNIQUE 约束 + 序列化器验证 |
| member_type 枚举 | choices=["member", "guest"] |
| guest_expires_at 仅 guest | 创建 member 时此字段为 null |
| 用户不可删除 | 用户一旦创建不可删除，仅访客可通过过期失效 |

### 状态转换

```
创建 → status=1  [有效，创建为原子操作：声纹注册+用户入库在同一请求中完成]
过期 → status=0  [仅 guest, Celery 自动标记]
```

> **注意**: 新用户创建为原子操作（声纹注册+用户入库在同一请求中完成），直接以 status=1 入库，确保系统中不存在无声纹的用户。用户一旦创建不可删除。访客过期后仅禁用（status=0），不从系统中移除。

---

## Entity: SpeakerProfile (已有，无修改)

**Table**: `voice_speaker_profile`
**关系**: OneToOne → SysUser.user_id

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | AutoField | PK |
| `user_id` | OneToOneField(SysUser) | 关联用户 |
| `gateway_speaker_id` | CharField(100, unique) | Gateway 声纹 ID |
| `name` | CharField(50) | 显示名称 |
| `quality_score` | FloatField(nullable) | 声纹质量 0.0~1.0 |
| `enrolled_at` | DateTimeField | 注册时间 |
| `created_at` | DateTimeField | 创建时间 |
| `updated_at` | DateTimeField | 更新时间 |

**015 使用方式**: 所有用户均有声纹（初始管理员自带声纹 + 创建流程强制注册），成员管理列表无需查询声纹状态。声纹注册 API 仅在用户创建流程步骤2中使用。

---

## Migration Plan

### Migration 0005: add_multiuser_fields

```python
operations = [
    migrations.AddField(
        model_name="sysuser",
        name="member_type",
        field=models.CharField(max_length=20, default="member",
                               choices=[("member", "成员"), ("guest", "访客")]),
    ),
    migrations.AddField(
        model_name="sysuser",
        name="guest_expires_at",
        field=models.DateTimeField(null=True, blank=True),
    ),
]
```

### ~~Migration 0006: populate_member_type~~ 已取消

开发完成后全量清库（PostgreSQL 所有表、MinIO、向量库、Langfuse），无需存量数据迁移。通过 `reset_all_data` management command 初始化带声纹的管理员测试账户。

---

## Query Patterns

### 成员列表

```python
from django.db.models import Case, When, BooleanField

queryset = SysUser.objects.filter(
    status=1,  # 仅返回有效用户，status=0 对系统完全不可见
).annotate(
    is_expired=Case(
        When(member_type="guest", guest_expires_at__lte=timezone.now(), then=True),
        default=False,
        output_field=BooleanField(),
    ),
)
if not include_expired:
    queryset = queryset.exclude(
        member_type="guest",
        guest_expires_at__lte=timezone.now(),
    )
queryset = queryset.order_by("is_expired", "created_time")  # 过期用户排在末尾
```

> **注意**: 始终过滤 `status=1`（status=0 用户对系统不可见）；不再需要 `is_deleted=False` 过滤（系统不支持删除），也不再需要 `has_speaker_profile` 注解（所有用户均有声纹）。

### 过期访客批量禁用

```python
SysUser.objects.filter(
    member_type="guest",
    guest_expires_at__lte=timezone.now(),
    status=1,
).update(status=0)
```
