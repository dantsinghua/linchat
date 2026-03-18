# Members 组件目录

> 015-family-multiuser: 家庭成员管理 UI 组件

## 文件清单

| 文件 | 用途 |
|------|------|
| `MemberSwitchModal.tsx` | 全屏模态框 — 展示成员列表，支持切换用户、添加用户入口；活跃成员可点击，过期访客灰色不可点击 |
| `CreateMemberWizard.tsx` | 两步向导 — Step 1: 成员/访客类型选择 + 用户名密码输入；Step 2: 声纹录音占位 UI（Phase 6 T046/T047 实现） |
| `avatarUtils.ts` | 头像工具函数 — `getAvatarColor(userId)` 按 user_id 取色、`getAvatarLetter(username)` 取首字母大写 |

## 依赖关系

| 依赖 | 说明 |
|------|------|
| `@/stores/memberStore` | Zustand Store — members 列表、authUserId、fetchMembers/switchMember |
| `@/services/memberService` | API 调用 — `createMember(formData)` POST /api/v1/members/ |
| `@/utils/crypto` | SM4 加密 — 密码传输前加密 |

## 组件交互

```
ChatPage
  └── MemberSwitchModal (点击头像触发)
        ├── 成员列表 → onSelect(userId, username) → switchMember → 刷新
        └── 添加用户 → onCreateUser → CreateMemberWizard
              ├── Step 1: 类型 + 用户名 + 密码
              └── Step 2: 声纹录音 → createMember API → onCreated → fetchMembers
```
