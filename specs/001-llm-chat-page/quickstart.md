# Quick Start Guide

大模型聊天页面快速启动指南。

---

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 后端运行时 |
| Node.js | 18+ | 前端运行时 |
| PostgreSQL | 15+ | 主数据库 |
| Redis | 7+ | 缓存 & LangGraph Checkpoint |
| Docker | 24+ | 容器化部署（可选） |

---

## 方式一：Docker Compose 启动

```bash
# 1. 克隆项目
git clone <repo-url>
cd linchat

# 2. 复制环境变量
cp .env.example .env
# 编辑 .env 配置 LLM 服务地址等

# 3. 启动所有服务
docker-compose up -d

# 4. 初始化数据库
docker-compose exec backend python manage.py migrate
docker-compose exec backend python manage.py init_admin

# 5. 访问
# 前端: http://localhost:3000
# 后端 API: http://localhost:8000/api/v1/
# Langfuse: http://localhost:3001
```

---

## 方式二：本地开发启动

### 1. 启动基础设施

```bash
# PostgreSQL + Redis
docker-compose up -d postgres redis

# 或使用本地安装的服务
# PostgreSQL: localhost:5432
# Redis: localhost:6379
```

### 2. 后端启动

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
export DATABASE_URL="postgresql://user:pass@localhost:5432/linchat"
export REDIS_URL="redis://localhost:6379"
export LLM_API_BASE="http://your-vllm-server:8000/v1"
export LLM_MODEL="your-model-name"
export SECRET_KEY="your-secret-key"

# 数据库迁移
python manage.py migrate

# 初始化 admin 账户
python manage.py init_admin
# 用户名: admin
# 密码: !9871229Qing

# 启动开发服务器
python manage.py runserver 0.0.0.0:8000
```

### 3. 前端启动

```bash
cd frontend

# 安装依赖
npm install

# 配置环境变量
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1" > .env.local

# 启动开发服务器
npm run dev
```

### 4. 访问应用

- 前端: http://localhost:3000
- 后端 API: http://localhost:8000/api/v1/
- API 文档: http://localhost:8000/api/docs/

---

## 环境变量说明

### 后端 (.env)

```bash
# 数据库
DATABASE_URL=postgresql://user:pass@localhost:5432/linchat

# Redis
REDIS_URL=redis://localhost:6379

# LLM 服务 (vLLM 或 OpenAI 兼容)
LLM_API_BASE=http://localhost:8080/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
LLM_API_KEY=EMPTY

# Django
SECRET_KEY=your-secret-key
DEBUG=true
ALLOWED_HOSTS=localhost,127.0.0.1

# Langfuse (可选)
LANGFUSE_SECRET_KEY=your-langfuse-secret
LANGFUSE_PUBLIC_KEY=your-langfuse-public
LANGFUSE_HOST=http://localhost:3001
```

### 前端 (.env.local)

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

---

## 初始账户

| 用户名 | 密码 | 说明 |
|--------|------|------|
| admin | !9871229Qing | 管理员账户（数据库初始化） |

---

## 验证安装

### 1. 验证后端

```bash
# 健康检查
curl http://localhost:8000/health/live
# 预期: {"status": "ok"}

# 获取验证码
curl http://localhost:8000/api/v1/auth/captcha
# 预期: {"code": 0, "data": {"captcha_id": "...", "captcha_image": "..."}}
```

### 2. 验证前端

访问 http://localhost:3000，应显示登录页面。

### 3. 完整流程测试

1. 访问 http://localhost:3000
2. 输入用户名 `admin`，密码 `!9871229Qing`
3. 输入验证码
4. 登录成功后进入聊天页面
5. 发送消息，验证流式响应

---

## 常见问题

### Q: LLM 服务连接失败

检查 `LLM_API_BASE` 配置是否正确，确保 vLLM 服务可访问。

```bash
curl http://your-vllm-server:8000/v1/models
```

### Q: Redis 连接失败

```bash
# 检查 Redis 服务
redis-cli ping
# 预期: PONG
```

### Q: 验证码不显示

检查前端 API 请求是否正确，查看浏览器 Network 面板。

### Q: Token 过期太快

Token 有两个过期机制：
- 1 小时无操作自动过期
- 24 小时绝对过期

用户操作会刷新 1 小时无操作计时器。

---

## 开发工具

### LangGraph Dev

```bash
# 启动 LangGraph Studio（开发调试）
pip install langgraph-cli
langgraph dev
```

### Langfuse 监控

访问 http://localhost:3001 查看 LLM 调用监控。

---

## 下一步

1. 阅读 [spec.md](./spec.md) 了解功能需求
2. 阅读 [data-model.md](./data-model.md) 了解数据结构
3. 阅读 [behavior-model.md](./behavior-model.md) 了解业务逻辑实现
4. 运行 `/speckit.tasks` 生成任务清单开始开发
