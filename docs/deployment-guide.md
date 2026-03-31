# LinChat 部署指南

> 本文档详细说明 LinChat 大模型聊天平台的完整部署流程，适用于全新部署和日常运维。

---

## 目录

1. [前置条件](#1-前置条件)
2. [环境准备](#2-环境准备)
3. [环境变量配置](#3-环境变量配置)
4. [Docker 服务部署](#4-docker-服务部署)
5. [应用服务部署](#5-应用服务部署)
6. [Nginx 反向代理配置](#6-nginx-反向代理配置)
7. [网络穿透配置](#7-网络穿透配置)
8. [服务管理命令](#8-服务管理命令)
9. [完整启动顺序](#9-完整启动顺序)
10. [健康检查](#10-健康检查)
11. [故障排查](#11-故障排查)
12. [参考文档](#12-参考文档)

---

## 1. 前置条件

### 1.1 系统要求

| 项目 | 最低版本 | 说明 |
|------|----------|------|
| 操作系统 | Ubuntu 20.04+ / Debian 11+ | 推荐 Linux |
| Python | 3.11+ | 后端运行时 |
| Node.js | 18+ | 前端构建和运行 |
| Docker | 24.0+ | 容器化基础设施 |
| Docker Compose | v2.0+ | 多容器编排 |
| Nginx | 1.18+ | 反向代理 |

### 1.2 硬件要求

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 20 GB | 50 GB+ (含 Docker 数据卷) |

### 1.3 端口规划

部署前确认以下端口未被占用：

| 端口 | 绑定地址 | 用途 |
|------|----------|------|
| 3784 | 0.0.0.0 | LinChat 前端 (Next.js) |
| 8002 | 0.0.0.0 | LinChat 后端 (uvicorn) |
| 8080 | 0.0.0.0 | Nginx 反向代理主入口 |
| 8081 | 0.0.0.0 | Nginx Langfuse 入口 |
| 5432 | 0.0.0.0 | PostgreSQL |
| 6379 | 0.0.0.0 | Redis |
| 8123 | 127.0.0.1 | ClickHouse HTTP (仅本地) |
| 9000 | 127.0.0.1 | ClickHouse Native (仅本地) |
| 9010 | 127.0.0.1 | MinIO S3 API (仅本地) |
| 9011 | 127.0.0.1 | MinIO Console (仅本地) |
| 3100 | 127.0.0.1 | Langfuse Web (仅本地) |
| 8124 | 0.0.0.0 | Home Assistant |
| 1880 | 127.0.0.1 | Node-RED (仅本地) |

```bash
# 检查端口占用
ss -tlnp | grep -E '(3784|8002|8080|8081|5432|6379|8123|9000|9010|3100|8124|1880)'
```

---

## 2. 环境准备

### 2.1 克隆项目

```bash
cd /home/dantsinghua/work
git clone <repository-url> linchat && cd linchat
```

### 2.2 Python 虚拟环境

```bash
# 创建虚拟环境
python3.11 -m venv /home/dantsinghua/work/linchat/linchat

# 激活（每次开发/运维前必须执行）
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 验证: 应输出 /home/dantsinghua/work/linchat/linchat/bin/python
which python

# 安装后端依赖
cd /home/dantsinghua/work/linchat/backend
pip install -r requirements.txt
```

> **警告**: 执行任何 Python/Django 命令之前，必须先激活虚拟环境。

### 2.3 前端依赖安装

```bash
cd /home/dantsinghua/work/linchat/frontend
npm install
npm run build    # 必须先构建再启动，不使用 npm run dev
```

### 2.4 数据库迁移

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py migrate    # 需 Docker PostgreSQL 已启动
```

---

## 3. 环境变量配置

项目共有三个环境变量文件，全部需要正确配置。

### 3.1 Docker Compose 环境变量 (`.env`)

位于项目根目录，供 `docker-compose.yml` 引用：

```bash
# PostgreSQL
POSTGRES_USER=postgres
POSTGRES_PASSWORD=linchat_123

# Redis
REDIS_PASSWORD=redis_linchat_123

# ClickHouse (Langfuse)
CLICKHOUSE_PASSWORD=langfuse_ch_123

# MinIO (Langfuse 对象存储)
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minio_123_secure

# Langfuse 认证密钥（首次部署时生成）
LANGFUSE_NEXTAUTH_SECRET=<openssl rand -base64 32>
LANGFUSE_SALT=<openssl rand -base64 32>
LANGFUSE_ENCRYPTION_KEY=<openssl rand -hex 32>

# Langfuse 初始管理员
LANGFUSE_INIT_ORG_ID=linchat-org
LANGFUSE_INIT_ORG_NAME=LinChat
LANGFUSE_INIT_PROJECT_ID=linchat-project
LANGFUSE_INIT_PROJECT_NAME=linchat-monitor
LANGFUSE_INIT_USER_EMAIL=admin@linchat.local
LANGFUSE_INIT_USER_PASSWORD=Admin@123456
LANGFUSE_INIT_USER_NAME=Admin
```

### 3.2 后端环境变量 (`backend/.env`)

```bash
DATABASE_URL=postgresql://postgres:linchat_123@localhost:5432/linchat
REDIS_URL=redis://:redis_linchat_123@localhost:6379/0
DJANGO_SECRET_KEY=<生成一个安全的随机字符串>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,www.greydan.xin
CORS_ALLOWED_ORIGINS=http://localhost:3784,http://127.0.0.1:3784,http://www.greydan.xin
SM4_SECRET_KEY=<你的 SM4 密钥>
LLM_API_BASE=<LLM API 地址>
LLM_API_KEY=<LLM API Key>
LLM_MODEL_NAME=<模型名称>
```

### 3.3 前端环境变量 (`frontend/.env.local`)

```bash
NEXT_PUBLIC_API_BASE_URL=/linchat/api/v1
NEXT_PUBLIC_SM4_KEY=<与后端 SM4_SECRET_KEY 一致>
```

### 3.4 安全要求

| 类别 | 要求 |
|------|------|
| 环境变量文件 | 已加入 `.gitignore`，禁止提交到版本控制 |
| API 密钥 | 使用国密 SM4 加密后存储在数据库 |
| 用户密码 | 使用国密 SM3 算法哈希存储 |
| 用户令牌 | 存储在 httpOnly Cookie 中，禁止使用 localStorage |

---

## 4. Docker 服务部署

Docker Compose 管理所有基础设施服务。

### 4.1 服务列表

| 容器名称 | 镜像 | 说明 |
|----------|------|------|
| linchat-postgres | 自定义 (pgvector/pgvector:pg15 + pg_jieba) | 主数据库，支持向量检索和中文全文搜索 |
| linchat-redis | redis/redis-stack-server:latest | 缓存 (DB0: LinChat, DB1: Langfuse, DB2: Celery) |
| linchat-clickhouse | clickhouse/clickhouse-server:24.3 | 分析库 (Langfuse v3 必需) |
| linchat-minio | minio/minio:latest | 对象存储 (Langfuse v3 必需) |
| linchat-minio-init | minio/mc:latest | 初始化 (创建 langfuse bucket，一次性任务) |
| linchat-langfuse-web | langfuse/langfuse:3 | Langfuse Web 监控平台 |
| linchat-langfuse-worker | langfuse/langfuse-worker:3 | Langfuse 后台任务处理 |
| linchat-homeassistant | ghcr.io/home-assistant/home-assistant:stable | Home Assistant 智能家居 |
| linchat-nodered | nodered/node-red:latest | Node-RED 可视化自动化 (仅内网) |

### 4.2 数据持久化

所有数据通过 Docker named volumes 持久化：`postgres_data`、`redis_data`、`clickhouse_data`、`clickhouse_logs`、`minio_data`、`homeassistant_config`、`nodered_data`。

### 4.3 自定义 PostgreSQL 镜像

基于 `pgvector/pgvector:pg15`，额外安装 pg_jieba 中文分词扩展：

- **pgvector**: 向量检索支持，用于 RAG 文档子代理的语义搜索
- **pg_jieba**: 中文全文搜索分词，编译失败时自动退化为默认 simple 分词

相关文件：
- Dockerfile: `docker/postgres/Dockerfile`
- 初始化脚本: `docker/postgres/init/01-create-databases.sh`、`02-create-extensions.sql`

### 4.4 启动与管理

```bash
cd /home/dantsinghua/work/linchat

# 首次部署（构建自定义镜像 + 启动）
docker compose up -d --build

# 后续启动
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f [服务名]

# 重启/停止
docker compose restart [服务名]
docker compose down              # 停止（保留数据卷）
# docker compose down -v         # 停止并删除数据卷（危险，不可恢复）
```

### 4.5 等待服务就绪

```bash
docker compose exec postgres pg_isready -U postgres        # PostgreSQL
docker compose exec redis redis-cli -a redis_linchat_123 ping  # Redis
curl -s http://127.0.0.1:8123/ping                         # ClickHouse
curl -sf http://127.0.0.1:9010/minio/health/live           # MinIO
```

---

## 5. 应用服务部署

应用服务包括后端 (uvicorn)、Celery Worker、Celery Beat 和前端 (Next.js)。

> **核心规则**: 必须使用 `scripts/services.sh` 管理，禁止手动 `nohup` 启动。

### 5.1 服务组成

| 服务 | 进程 | 端口 | 日志 | PID 文件 |
|------|------|------|------|----------|
| 后端 | `uvicorn core.asgi:application` | 8002 | `/tmp/linchat-backend.log` | `.pids/backend.pid` |
| Celery Worker | `celery -A core worker` | -- | `/tmp/linchat-celery-worker.log` | `.pids/celery-worker.pid` |
| Celery Beat | `celery -A core beat` | -- | `/tmp/linchat-celery-beat.log` | `.pids/celery-beat.pid` |
| 前端 | `npm run start -- -p 3784` | 3784 | `/tmp/linchat-frontend.log` | `.pids/frontend.pid` |

### 5.2 后端要求

- **必须**使用 uvicorn ASGI 模式启动，**禁止**使用 `python manage.py runserver`（WSGI 不支持异步 SSE）
- 使用 `PYTHONUNBUFFERED=1` 确保日志实时输出
- 使用 `setsid` 创建新进程组，便于子进程管理

### 5.3 Celery 要求

- **Worker**: 处理异步任务（数据同步、文档解析、记忆摘要等）
- **Beat**: 定时任务调度器（每日摘要等 cron 任务）
- Broker 使用 Redis DB2

### 5.4 前端要求

- **必须**先 `npm run build` 构建，再 `npm run start` 运行
- **禁止**使用 `npm run dev`，所有环境均按生产环境对待

### 5.5 启动

```bash
cd /home/dantsinghua/work/linchat
docker compose ps                  # 确认 Docker 服务已启动
./scripts/services.sh start        # 启动所有应用服务
./scripts/services.sh status       # 查看状态
```

---

## 6. Nginx 反向代理配置

Nginx 作为统一入口，将请求路由到对应的后端服务。

### 6.1 配置文件

```
/etc/nginx/sites-available/deeptutor    # 主配置
/etc/nginx/sites-enabled/deeptutor      # 软链接
```

### 6.2 Upstream 定义

```nginx
upstream linchat_frontend { server 127.0.0.1:3784; keepalive 32; }
upstream linchat_backend  { server 127.0.0.1:8002; keepalive 32; }
upstream langfuse_web     { server 127.0.0.1:3100; keepalive 32; }
```

### 6.3 路由规则

**主服务 (端口 3782/8080)**:

| 路径 | 目标 | 说明 |
|------|------|------|
| `/linchat/api/*` | linchat_backend (8002) | URL 重写 `/linchat/api/` -> `/api/` |
| `/linchat/ws/*` | linchat_backend (8002) | WebSocket (语音交互) |
| `/linchat/*` | linchat_frontend (3784) | 前端页面 |
| `/linchat/_next/*` | linchat_frontend (3784) | 静态资源（启用缓存） |

**Langfuse (端口 8081)**: 全部转发到 langfuse_web (3100)。

### 6.4 关键配置

**SSE 支持**（后端 API 路由必须配置，否则流式响应断开）：

```nginx
location /linchat/api/ {
    rewrite ^/linchat/api/(.*)$ /api/$1 break;
    proxy_pass http://linchat_backend;
    proxy_buffering off;           # 关闭缓冲
    proxy_cache off;
    chunked_transfer_encoding on;
    add_header X-Accel-Buffering no;
    proxy_read_timeout 86400s;     # 长连接超时
    proxy_send_timeout 86400s;
}
```

**WebSocket 支持**（语音交互路由必须配置）：

```nginx
location /linchat/ws/ {
    rewrite ^/linchat/ws/(.*)$ /ws/$1 break;
    proxy_pass http://linchat_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

**文件上传限制**: `client_max_body_size 100M;`

### 6.5 管理命令

```bash
sudo nginx -t             # 测试配置
sudo nginx -s reload      # 重载（不中断服务）
sudo systemctl status nginx
```

---

## 7. 网络穿透配置

通过 frpc + wstunnel 实现内网服务的公网访问，具有抗 DPI 检测能力。

### 7.1 架构

```
公网 HTTPS --> frps (infra.greydan.xin)
    --> wss://infra.greydan.xin:443
    --> wstunnel client (127.0.0.1:7443)
    --> frpc --> Nginx (8080)
    --> /linchat/api/* --> 后端 (8002)
    --> /linchat/*     --> 前端 (3784)
```

### 7.2 wstunnel

将 TCP 流量封装在 WebSocket over TLS 中，穿越 DPI 防火墙。

- systemd 服务: `/etc/systemd/system/wstunnel.service`
- 启动命令: `wstunnel client -L tcp://127.0.0.1:7443:127.0.0.1:7000 wss://infra.greydan.xin:443`

### 7.3 frpc

配置文件: `/home/dantsinghua/frp/frpc.toml`

通过 wstunnel 建立的本地隧道连接远端 frps：

```toml
serverAddr = "127.0.0.1"
serverPort = 7443

[[proxies]]
name = "linchat-web"
type = "http"
localIP = "127.0.0.1"
localPort = 8080
customDomains = ["www.greydan.xin"]
locations = ["/linchat"]
```

### 7.4 systemd 管理

两个服务均由 systemd 管理，**禁止**手动 nohup 启动：

```bash
sudo systemctl start wstunnel && sudo systemctl start frpc   # 启动（wstunnel 先于 frpc）
sudo systemctl status wstunnel                                # 查看状态
sudo systemctl status frpc
sudo systemctl restart frpc                                   # 修改 frpc.toml 后重启
journalctl -u frpc -f                                         # 实时日志
journalctl -u wstunnel -f
```

### 7.5 公网访问地址

| 服务 | 地址 |
|------|------|
| LinChat | `https://www.greydan.xin/linchat` |
| LinChat API | `https://www.greydan.xin/linchat/api/v1` |
| Langfuse | `http://www.greydan.xin:8081` |
| Home Assistant | `https://ha.greydan.xin` |

---

## 8. 服务管理命令

### 8.1 services.sh 用法

`scripts/services.sh` 是应用服务的**唯一**管理入口：

```bash
cd /home/dantsinghua/work/linchat
./scripts/services.sh start      # 启动全部（后端 + Celery + 前端）
./scripts/services.sh stop       # 停止全部
./scripts/services.sh restart    # 重启全部
./scripts/services.sh status     # 状态（含 Docker 服务）
```

### 8.2 工作原理

- **PID 追踪**: 每个进程的 PID 记录在 `.pids/` 目录下
- **进程组管理**: `setsid` 创建新进程组，`stop` 杀掉整个进程组
- **孤儿清理**: `stop` 额外用 `pgrep` 兜底清理残余进程
- **幂等启动**: 已运行的进程不会重复启动

### 8.3 禁止手动 nohup

手动 `nohup uvicorn/celery/npm &` 会导致：PID 文件缺失 -> `services.sh` 无法追踪 -> 孤儿进程积累 -> 端口冲突。历史教训：曾因此积累 20+ 个孤儿进程。

### 8.4 日志查看

```bash
tail -f /tmp/linchat-backend.log         # 后端
tail -f /tmp/linchat-celery-worker.log   # Celery Worker
tail -f /tmp/linchat-celery-beat.log     # Celery Beat
tail -f /tmp/linchat-frontend.log        # 前端
```

### 8.5 代码变更后的操作

**前端代码变更**：需重新构建再重启：

```bash
cd /home/dantsinghua/work/linchat/frontend && npm run build
cd /home/dantsinghua/work/linchat && ./scripts/services.sh restart
```

**后端代码变更**：直接重启（如有模型变更需先执行迁移）：

```bash
# 有模型变更时
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py makemigrations && python manage.py migrate

# 重启
cd /home/dantsinghua/work/linchat && ./scripts/services.sh restart
```

---

## 9. 完整启动顺序

全新部署或服务器重启后，按以下顺序执行：

```bash
# 1. Docker 基础设施
cd /home/dantsinghua/work/linchat
docker compose up -d
docker compose ps    # 等待所有健康检查通过

# 2. Nginx
sudo systemctl start nginx

# 3. 网络穿透（wstunnel 必须先于 frpc）
sudo systemctl start wstunnel
sudo systemctl start frpc

# 4. 数据库迁移（首次或模型变更后）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py migrate

# 5. 前端构建（首次或代码变更后）
cd /home/dantsinghua/work/linchat/frontend
npm run build

# 6. 应用服务
cd /home/dantsinghua/work/linchat
./scripts/services.sh start

# 7. 验证
./scripts/services.sh status
```

---

## 10. 健康检查

### 10.1 Docker 服务

```bash
docker compose ps                                               # 全部状态
docker compose exec postgres pg_isready -U postgres             # PostgreSQL
docker compose exec redis redis-cli -a redis_linchat_123 ping   # Redis
curl -s http://127.0.0.1:8123/ping                              # ClickHouse
curl -sf http://127.0.0.1:9010/minio/health/live                # MinIO
```

### 10.2 应用服务

```bash
./scripts/services.sh status                                             # 全部状态
curl -s http://localhost:8002/api/v1/health/                             # 后端直连
curl -so /dev/null -w "%{http_code}" http://localhost:3784/linchat/      # 前端直连
curl -so /dev/null -w "%{http_code}" http://localhost:8080/linchat/      # 经 Nginx 访问
curl -so /dev/null -w "%{http_code}" http://localhost:8080/linchat/api/v1/health/
```

### 10.3 系统服务

```bash
sudo systemctl status nginx
sudo systemctl status wstunnel
sudo systemctl status frpc
ss -tlnp | grep -E '(3784|8002|8080|5432|6379)'   # 核心端口
```

---

## 11. 故障排查

### 11.1 常见问题速查

| 症状 | 可能原因 | 排查 |
|------|----------|------|
| 前端 404 | 未执行 `npm run build` | 检查 `frontend/.next/` 目录 |
| API 502 | 后端未启动 | `./scripts/services.sh status`；查 `/tmp/linchat-backend.log` |
| 数据库连接失败 | PostgreSQL 未运行 | `docker compose ps postgres` |
| Redis 连接失败 | Redis 未运行或密码错误 | `docker compose exec redis redis-cli -a <password> ping` |
| 公网无法访问 | 穿透链路断开 | 依次检查: wstunnel -> frpc -> Nginx |
| SSE 流断开 | Nginx 缓冲未关闭 | 确认 `proxy_buffering off` |
| 卡在 "AI 正在生成" | 后端异常处理失败 | 查后端日志 |
| Celery 任务不执行 | Worker 未启动或 Broker 不通 | 查 Worker 日志和 Redis DB2 |
| Langfuse 无数据 | Worker 未运行 | `docker compose logs langfuse-worker` |

### 11.2 后端启动失败

```bash
tail -50 /tmp/linchat-backend.log

# 常见原因: 虚拟环境未激活 / 数据库未迁移 / 端口被占用 / 环境变量缺失
# 手动调试:
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002
```

### 11.3 前端构建失败

```bash
cd /home/dantsinghua/work/linchat/frontend
npm run build 2>&1 | tail -30

# 常见原因: node_modules 损坏 / TypeScript 类型错误 / .env.local 缺失
# 修复: rm -rf node_modules && npm install
```

### 11.4 公网访问排查链路

按顺序逐层排查：

```bash
# 1. wstunnel
sudo systemctl status wstunnel

# 2. frpc
sudo systemctl status frpc
journalctl -u frpc --since "10 min ago" | grep -E "(error|failed)"

# 3. Nginx
ss -tlnp | grep 8080
sudo nginx -t

# 4. 后端/前端
curl -s http://localhost:8002/api/v1/health/
curl -so /dev/null -w "%{http_code}" http://localhost:3784/linchat/

# 5. Nginx 代理
curl -so /dev/null -w "%{http_code}" http://localhost:8080/linchat/
```

### 11.5 进程残留清理

```bash
# services.sh stop 会自动清理孤儿进程
./scripts/services.sh stop

# 如仍有残留，手动检查
pgrep -af "uvicorn core.asgi"
pgrep -af "celery -A core"
pgrep -af "next-server"
```

### 11.6 数据库问题

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python manage.py showmigrations                # 迁移状态
python manage.py dbshell                       # 数据库连接

# 检查扩展
docker compose exec postgres psql -U postgres -d linchat \
    -c "SELECT extname, extversion FROM pg_extension;"
```

---

## 12. 参考文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 系统架构 | [system-architecture.md](system-architecture.md) | 系统整体架构设计 |
| 配置指南 | [configuration-guide.md](configuration-guide.md) | 详细配置项说明 |
| Gateway 集成 | [linchat-integration-guide.md](linchat-integration-guide.md) | LLM Gateway 集成说明 |
| 多模态 API | [multimodal-api-guide.md](multimodal-api-guide.md) | 多模态接口文档 |
| TTS WebSocket | [tts-websocket-api.md](tts-websocket-api.md) | TTS 流式接口文档 |
| 项目宪法 | [../.specify/memory/constitution.md](../.specify/memory/constitution.md) | 不可违背的开发原则 |

---

*本文档随项目演进持续更新。最后更新: 2026-03-31*
