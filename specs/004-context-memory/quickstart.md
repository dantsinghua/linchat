# 快速启动指南 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-30

---

## 前置条件

- M1a 已完成（`ModelConfig` 表可用，含 `embedding_dimensions` 字段）
- Docker 服务运行中（PostgreSQL、Redis）
- Python 虚拟环境已激活

## 1. 安装 pgvector 扩展

```bash
# 进入 PostgreSQL 容器安装 pgvector
docker exec -it linchat-postgres bash
apk add --no-cache git build-base clang15 llvm15
cd /tmp
git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git
cd pgvector
make && make install
exit

# 在数据库中启用扩展
docker exec -it linchat-postgres psql -U postgres -d linchat -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> 注意：也可以使用预装 pgvector 的 PostgreSQL 镜像 `pgvector/pgvector:pg15` 替换当前 `postgres:15-alpine`。

## 2. 安装 Python 依赖

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 新增依赖（添加到 requirements.txt 后）
pip install tiktoken>=0.7.0
pip install pgvector>=0.3.0
pip install celery>=5.3.0
pip install django-celery-beat>=2.5.0
```

## 3. 创建 Django App

```bash
cd /home/dantsinghua/work/linchat/backend
python manage.py startapp memory apps/memory
```

在 `core/settings.py` 的 `INSTALLED_APPS` 中添加：
```python
INSTALLED_APPS = [
    # ...existing apps...
    'django.contrib.postgres',    # PostgreSQL 全文搜索支持
    'pgvector.django',            # pgvector Django 集成
    'django_celery_beat',         # Celery Beat 调度
    'apps.memory',                # 记忆管理模块
]
```

## 4. 数据库迁移

```bash
python manage.py makemigrations memory
python manage.py migrate
```

## 5. 配置 Celery

创建 `backend/core/celery.py`：
```python
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('linchat')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
```

在 `core/settings.py` 添加 Celery 配置：
```python
CELERY_BROKER_URL = 'redis://localhost:6379/2'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/2'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'

# Beat Schedule
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    'retry-failed-embeddings': {
        'task': 'apps.memory.tasks.retry_failed_embeddings',
        'schedule': 300.0,  # 每 5 分钟
    },
    'daily-summary': {
        'task': 'apps.memory.tasks.generate_daily_summary',
        'schedule': crontab(hour=0, minute=0),
    },
    'monthly-summary': {
        'task': 'apps.memory.tasks.generate_monthly_summary',
        'schedule': crontab(day_of_month=1, hour=0, minute=0),
    },
}
```

## 6. 启动服务

```bash
# 终端 1：后端（ASGI）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload

# 终端 2：Celery Worker
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
celery -A core worker -l info

# 终端 3：Celery Beat
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
celery -A core beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## 7. 验证

```bash
# 检查 pgvector 扩展
docker exec -it linchat-postgres psql -U postgres -d linchat -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"

# 检查迁移
python manage.py showmigrations memory

# 检查 Celery 连接
celery -A core inspect ping
```

## 8. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/memories/` | 记忆列表 |
| POST | `/api/v1/memories/` | 创建记忆 |
| GET | `/api/v1/memories/{id}/` | 记忆详情 |
| PUT | `/api/v1/memories/{id}/` | 更新记忆 |
| DELETE | `/api/v1/memories/{id}/` | 删除记忆 |
| POST | `/api/v1/memories/search/` | 语义搜索 |

---

*文档版本：v1.0*
*创建日期：2026-01-30*
