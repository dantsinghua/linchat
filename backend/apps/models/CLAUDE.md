# Models 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> LLM 模型配置管理：3 种模型（tool/multimodal/embedding）的查看和修改，SM4 加密密钥存储。

## 文件清单

| 文件 | 职责 |
|------|------|
| `models.py` | ModelConfig 数据模型（表 `model`，3 种类型） |
| `views.py` | ModelListView / ModelDetailView（仅管理员） |
| `services.py` | SM4 加解密、API Key 脱敏、内部明文接口 |
| `repositories.py` | get_all / get_by_id / get_active_by_type / update |
| `serializers.py` | ModelResponseSerializer + ModelUpdateSerializer |
| `permissions.py` | IsAdminUser（request.user_type == "admin"） |
| `urls.py` | `/models/`、`/models/<id>/` |

## 核心模型 ModelConfig（表 `model`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | AutoField (PK) | 主键 |
| `type` | CharField(20) | `tool` / `multimodal` / `embedding` |
| `name` / `url` | 模型名称 / API 地址 | |
| `api_key` | CharField(500) | SM4 加密存储 |
| `max_context_window` / `max_input_tokens` / `max_output_tokens` | 容量参数 | |
| `temperature` / `top_p` / `frequency_penalty` / `presence_penalty` | 采样参数（可选） | |
| `embedding_dimensions` | 向量维度（仅 embedding） | |
| `is_active` | 是否激活（系统管理） | |

计算属性: `effective_context_window` = `max_context_window * 0.9`

## API 端点

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/v1/models/` | AdminUser | 所有模型（Key 脱敏） |
| GET | `/api/v1/models/<id>/` | AdminUser | 单个模型 |
| PUT | `/api/v1/models/<id>/` | AdminUser | 更新模型 |

## API Key 三态处理

| 场景 | 形态 |
|------|------|
| 数据库 | SM4 密文 |
| GET 响应 | 脱敏（`sk-a****bKey`） |
| PUT 请求 | 含 `****` 保留原值，否则加密存新值（>=12 字符） |
| 内部调用 | 明文（`get_active_model` decrypt=True） |

## 内部接口（其他模块调用）

```python
from apps.models.services import model_service
config = model_service.get_active_model("tool")        # 工具模型
config = model_service.get_active_model("multimodal")   # 多模态模型
config = model_service.get_active_model("embedding")    # 向量模型
# 返回 {id, type, name, url, api_key(明文), max_*, ...}
```

调用方: `apps.chat` / `apps.graph` / `apps.memory` / `apps.voice`

## 关键依赖

| 依赖 | 说明 |
|------|------|
| `apps.users.crypto` | SM4 加解密 |
| `apps.common.responses` | 统一响应格式 |
| `apps.common.middleware` | TokenAuthMiddleware 设置 request.user_type |

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/models/ -v
```


<claude-mem-context>
# Recent Activity

### Mar 11, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1629 | 8:34 AM | 🔵 | Complete ModelConfig Schema Definition | ~431 |
| #1627 | 8:33 AM | 🔵 | ModelConfig Database Schema and Security Implementation | ~361 |
</claude-mem-context>