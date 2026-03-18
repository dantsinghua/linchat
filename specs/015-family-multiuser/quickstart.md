# Quickstart: 家庭多用户系统

**Feature**: 015-family-multiuser | **Date**: 2026-03-11

---

## 前置条件

```bash
# 确认在正确分支
git branch --show-current  # 应显示 015-family-multiuser

# Docker 服务运行中
docker compose ps  # PostgreSQL + Redis 必须 healthy

# 虚拟环境激活
source /home/dantsinghua/work/linchat/linchat/bin/activate
which python  # 应显示 .../linchat/bin/python
```

---

## 后端开发

### 1. 模型扩展与迁移

```bash
cd /home/dantsinghua/work/linchat/backend

# 修改 apps/users/models.py 后生成迁移
python manage.py makemigrations users --name add_multiuser_fields
# 仅新增 member_type + guest_expires_at 两个字段（无 is_deleted）

python manage.py migrate
```

### 2. 运行测试

```bash
# 单模块测试
pytest tests/users/ -v

# 全量测试（确认不破坏现有功能）
pytest --tb=short
```

### 3. 启动后端验证

```bash
PYTHONUNBUFFERED=1 uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 4. API 手动验证

```bash
# 登录获取 Cookie
curl -c cookies.txt -X POST http://localhost:8002/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"anlin","password":"<sm4>","captcha_id":"<id>","captcha_code":"<code>"}'

# 获取成员列表
curl -b cookies.txt http://localhost:8002/api/v1/members/

# 创建访客（固定 7 天有效期，后端自动计算，无需传 guest_expires_days）
curl -b cookies.txt -X POST http://localhost:8002/api/v1/members/ \
  -H "Content-Type: application/json" \
  -d '{"username":"guest1","password":"<sm4>","member_type":"guest"}'

# 切换视角查看其他用户的消息
curl -b cookies.txt -H "X-Target-User-Id: 5" \
  http://localhost:8002/api/v1/chat/messages/
```

---

## 前端开发

### 1. 创建组件目录

```bash
cd /home/dantsinghua/work/linchat/frontend
mkdir -p src/components/members
```

### 2. 开发关键文件

| 文件 | 优先级 | 说明 |
|------|--------|------|
| `src/stores/memberStore.ts` | P1 | Zustand store: targetUserId, members list |
| `src/services/memberService.ts` | P1 | API 调用：list, create, delete |
| `src/services/api.ts` | P1 | 请求拦截器添加 X-Target-User-Id |
| `src/components/members/MemberSwitchModal.tsx` | P1 | 用户列表 + 切换模态框 |
| `src/components/chat/MessageInput.tsx` | P1 | 左侧头像按钮入口 |
| `src/components/members/CreateMemberWizard.tsx` | P2 | 分步创建引导 |
| `src/components/members/VoiceprintRecorder.tsx` | P2 | 声纹录音 UI |

### 3. 构建与测试

```bash
npm run build    # 构建
npm run start -- -p 3784  # 启动
```

---

## Celery 任务

```bash
# 确认 celery worker 和 beat 运行
./scripts/services.sh status

# 手动测试访客过期任务
cd /home/dantsinghua/work/linchat/backend
python -c "
from apps.users.tasks import expire_guests
expire_guests()
"
```

---

## 验收测试清单

| # | 场景 | 验证方法 |
|---|------|----------|
| 1 | 成员登录看到头像切换按钮 | 浏览器 UI |
| 2 | 访客登录不显示切换按钮 | 浏览器 UI |
| 3 | 创建成员（含声纹注册） | 模态框分步操作 |
| 4 | 创建访客（含声纹注册） | 模态框分步操作 |
| 5 | 切换到其他用户，聊天历史变化 | 浏览器 UI |
| 6 | 切换后发消息，消息归属目标用户 | Langfuse 追踪 |
| 7 | 访客过期后无法登录 | 等待过期 / 手动修改 DB |
| 8 | Web 语音模式以 ambient 连接 | WebSocket 日志 |
