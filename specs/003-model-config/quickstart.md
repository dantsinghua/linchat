# 快速启动：模型配置管理

**特性**：003-model-config
**日期**：2026-01-29

## 前置条件

1. Docker 服务运行中 (`docker compose up -d`)
2. Python 虚拟环境已激活:
   ```bash
   source /home/dantsinghua/work/linchat/linchat/bin/activate
   ```

## 后端开发

### 1. 创建 Django App

```bash
cd /home/dantsinghua/work/linchat/backend
python manage.py startapp models apps/models
```

### 2. 注册 App

在 `core/settings.py` 的 `INSTALLED_APPS` 中添加 `'apps.models'`

### 3. 创建并执行迁移

```bash
cd /home/dantsinghua/work/linchat/backend
python manage.py makemigrations models
python manage.py migrate
```

### 4. 验证迁移

```bash
python manage.py shell -c "from apps.models.models import Model; print(Model.objects.count())"
# 期望输出: 2
```

### 5. 启动后端

```bash
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 6. 验证 API

> **⚠️ 权限要求**：所有模型配置 API 仅限管理员用户（admin 角色）访问。使用非管理员用户的 Cookie 将返回 403。

```bash
# 获取所有模型（需要使用管理员用户登录获取的 Cookie）
curl -b cookies.txt http://localhost:8002/api/v1/models/

# 验证非管理员返回 403
# curl -b regular_user_cookies.txt http://localhost:8002/api/v1/models/
# 期望: {"code": 403, "message": "权限不足，仅管理员可访问", "data": null}

# 更新模型配置（管理员 Cookie）
curl -X PUT -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"name":"deepseek-v3","url":"https://api.example.com/v1","api_key":"sk-new-key","max_context_window":65536,"max_input_tokens":32768,"max_output_tokens":8192}' \
  http://localhost:8002/api/v1/models/1/
```

## 前端开发

### 1. 创建设置页面

新建 `frontend/src/app/settings/page.tsx`

### 2. 构建并启动前端

```bash
cd /home/dantsinghua/work/linchat/frontend
npm run build
npm run start -- -p 3784
```

### 3. 访问设置页面

浏览器打开：`http://localhost:3784/linchat/settings`（需管理员登录）

## 测试

### 后端测试

```bash
cd /home/dantsinghua/work/linchat/backend
pytest tests/apps/models/ -v
```

### 前端测试

```bash
cd /home/dantsinghua/work/linchat/frontend
npm test -- --testPathPattern=settings
```

## 关键文件清单

| 文件 | 作用 |
|------|------|
| `backend/apps/models/models.py` | Model 数据模型 |
| `backend/apps/models/services.py` | 业务逻辑（加解密、参数构造） |
| `backend/apps/models/repositories.py` | 数据访问层 |
| `backend/apps/models/serializers.py` | 请求/响应序列化 |
| `backend/apps/models/views.py` | API 视图 |
| `backend/apps/models/permissions.py` | 自定义权限类（IsAdminUser） |
| `backend/apps/chat/agent.py` | 改造：从数据库读取配置 |
| `frontend/src/app/settings/page.tsx` | 设置页面 |
| `frontend/src/components/settings/ModelConfigCard.tsx` | 模型配置卡片（按类型分组） |
| `frontend/src/services/modelService.ts` | 模型配置 API |
| `frontend/src/stores/modelStore.ts` | 模型配置状态 |
