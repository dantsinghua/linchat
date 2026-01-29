# apps/models — 模型配置管理

## 模块职责

管理 LLM 模型配置（语言模型 + 嵌入模型），支持在线查看和修改，配置修改后即时生效无需重启。

## 分层结构

```
views.py          → 视图层：HTTP 请求响应（GET 列表/详情、PUT 更新）
services.py       → 服务层：业务逻辑（SM4 加解密、API Key 脱敏、数据转换）
repositories.py   → 数据层：ORM 操作封装
models.py         → 数据模型：ModelConfig（16 字段 + 计算属性）
serializers.py    → 序列化器：请求校验和响应格式化
permissions.py    → 权限控制：IsAdminUser（仅管理员可访问）
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/models/` | 获取所有模型配置（API Key 脱敏） |
| GET | `/api/v1/models/<id>/` | 获取单个模型配置 |
| PUT | `/api/v1/models/<id>/` | 更新模型配置 |

## 数据模型

- 表名：`model`
- 类型：`language`（语言模型）、`embedding`（嵌入模型）
- API Key：SM4 加密存储，GET 响应脱敏展示
- 计算属性：`effective_context_window = int(max_context_window * 0.9)`

## 内部接口

`model_service.get_active_model(type)` — 按类型获取激活模型配置（API Key 解密为明文），供 `apps.chat` 模块调用。
