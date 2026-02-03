# Models 模块开发指南

> 本文件为 `apps/models` 模型配置管理模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

管理 LLM 模型配置（语言模型 + 嵌入模型），支持在线查看和修改，配置修改后即时生效无需重启。

**当前阶段（M1a）**：固定 2 条记录（`language` + `embedding`），仅支持查看和修改，不支持创建、删除、停用。

---

## 目录结构

```
apps/models/
├── models.py          # ModelConfig 数据模型（16 字段 + 计算属性）
├── views.py           # HTTP 视图（GET 列表/详情、PUT 更新）
├── services.py        # 业务逻辑（SM4 加解密、API Key 脱敏、数据转换）
├── repositories.py    # 数据访问层（ORM 操作封装）
├── serializers.py     # DRF 序列化器（请求校验 + 响应格式化）
├── permissions.py     # 权限控制（IsAdminUser）
├── urls.py            # 路由配置
├── apps.py            # Django App 配置
├── admin.py           # Django Admin（空）
├── tests.py           # 测试文件（待实现）
├── README.md          # 模块说明文档
└── migrations/
    ├── 0001_create_model_config.py  # 建表迁移
    └── 0002_seed_model_configs.py   # 种子数据（从环境变量初始化）
```

---

## 核心数据模型

### ModelConfig（表名：`model`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | AutoField (PK) | 自增主键 |
| `type` | CharField | `language` 或 `embedding` |
| `name` | CharField | 模型名称 |
| `url` | CharField | API 基础地址 |
| `api_key` | CharField | API 密钥（**SM4 加密存储**） |
| `max_context_window` | PositiveIntegerField | 最大上下文窗口（token 数） |
| `max_input_tokens` | PositiveIntegerField | 最大输入 token 数 |
| `max_output_tokens` | PositiveIntegerField | 最大输出 token 数 |
| `temperature` | FloatField (NULL) | 温度参数（NULL=使用模型默认值） |
| `top_p` | FloatField (NULL) | Top-P 采样 |
| `frequency_penalty` | FloatField (NULL) | 频率惩罚 |
| `presence_penalty` | FloatField (NULL) | 存在惩罚 |
| `embedding_dimensions` | PositiveIntegerField (NULL) | 向量维度（仅 embedding 类型） |
| `is_active` | BooleanField | 是否激活（系统管理，不可编辑） |
| `created_at` | DateTimeField (auto_now_add) | 创建时间 |
| `updated_at` | DateTimeField (auto_now) | 更新时间 |

**计算属性**：

- `effective_context_window` → `int(max_context_window * 0.9)`（预留 10% 安全余量）
- `masked_api_key` → 脱敏展示：长度>8 时 `前4位****后4位`，否则全脱敏

---

## API 端点

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/v1/models/` | AdminUser | 获取所有模型配置（API Key 脱敏） |
| GET | `/api/v1/models/<id>/` | AdminUser | 获取单个模型配置 |
| PUT | `/api/v1/models/<id>/` | AdminUser | 更新模型配置 |

所有端点仅管理员可访问，普通用户返回 403。

---

## 核心业务逻辑

### API Key 三态处理

API Key 在不同场景下有不同形态：

| 场景 | 形态 | 说明 |
|------|------|------|
| 数据库存储 | SM4 密文 | `sm4_encrypt(明文)` |
| GET 响应 | 脱敏展示 | `sk-a****bKey` |
| PUT 请求 | 脱敏值或新值 | 含 `****` 则保留原值，否则加密存储新值 |
| 内部调用 | 明文 | `sm4_decrypt(密文)` |

### 更新流程

```
PUT 请求 → ModelUpdateSerializer 校验
  → 检查 api_key：含 "****" → 移除（保留原值）；否则 → sm4_encrypt(新值)
  → 移除不可修改字段（type/is_active/id/created_at/updated_at）
  → model_repo.update() → 返回脱敏数据
```

### 内部接口（供其他模块调用）

```python
from apps.models.services import model_service

# 获取激活的语言模型（API Key 解密为明文）
config = model_service.get_active_model('language')
# config = {id, type, name, url, api_key(明文), max_context_window, ...}
```

此接口供 `apps.chat` 模块调用，不受管理员权限限制。

---

## 分层架构

### 视图层（views.py）

- `ModelListView`：GET 列表
- `ModelDetailView`：GET 详情 + PUT 更新
- 仅处理 HTTP 请求/响应，业务逻辑委托给 services

### 序列化层（serializers.py）

- `ModelResponseSerializer`：GET 响应格式化（含脱敏 API Key + 计算属性）
- `ModelUpdateSerializer`：PUT 请求校验
  - `validate_api_key()`：脱敏值跳过，新密钥最少 12 字符
  - `validate_max_context_window()`：确保整数
  - `validate()`：跨字段校验（language 模型的 `embedding_dimensions` 必须为 NULL）

### 服务层（services.py）

`ModelService` 类，所有方法为 `@staticmethod`：

| 方法 | 说明 |
|------|------|
| `get_all_models()` | 查询全部模型（API Key 脱敏） |
| `get_model_by_id(model_id)` | 查询单个（API Key 脱敏） |
| `update_model(model_id, data)` | 更新模型配置 |
| `get_active_model(model_type)` | 获取激活模型（API Key **明文**，内部调用） |

辅助函数：`_mask_api_key()` / `_model_to_dict()`

### 数据访问层（repositories.py）

`ModelRepository` 类，所有方法为 `@staticmethod`：

| 方法 | 说明 |
|------|------|
| `get_all()` | 查询全部（按 id 排序） |
| `get_by_id(model_id)` | 按 ID 查询 |
| `get_active_by_type(model_type)` | 按类型获取激活模型 |
| `update(model, **kwargs)` | 更新字段并保存 |

提供全局实例 `model_repo` 供外部导入。

### 权限层（permissions.py）

- `IsAdminUser`：基于 `request.user_type == "admin"` 判定（由 TokenAuthMiddleware 设置）

---

## 数据库迁移

| 迁移 | 内容 |
|------|------|
| `0001_create_model_config` | 创建 `model` 表，定义全部 16 字段 |
| `0002_seed_model_configs` | 种子数据：创建 language + embedding 两条记录，从环境变量 `LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL_NAME` 读取初始值 |

---

## 开发规范

### 分层职责（严格遵守）

- **views.py**：仅做 HTTP 解析和响应包装，禁止业务逻辑
- **services.py**：封装所有业务逻辑，是核心层
- **repositories.py**：封装 ORM 操作，提供全局实例
- **serializers.py**：请求校验和响应格式化

### 同步模式

本模块当前使用**同步**模式（与 `apps/users` 的异步模式不同），因为：
- 模型配置操作频率低
- 仅管理员使用
- 无需高并发支持

### 安全红线

- API Key **必须** SM4 加密存储，禁止明文入库
- GET 响应**必须**脱敏展示，禁止返回明文或密文
- 更新时含 `****` 的值必须保留原值，防止覆盖
- `is_active` / `type` 等系统字段禁止通过 API 修改

### 编码风格

- 所有方法使用 `@staticmethod`，提供模块级全局实例
- 类型注解：所有公共函数必须有完整类型声明
- 文档字符串：Google 风格（参数、返回值、异常）
- 异常处理：解密失败返回空字符串而非抛出异常（防御性编程）
- 日志：关键操作使用 `logging` 记录

---

## 关键依赖

| 依赖 | 位置 | 说明 |
|------|------|------|
| SM4 加解密 | `apps/users/crypto.py` | `sm4_encrypt()` / `sm4_decrypt()` |
| 统一响应 | `apps/common/responses.py` | `ApiResponse` 格式 |
| Token 中间件 | `apps/common/middleware.py` | 设置 `request.user_type` |

### 被依赖

- `apps.chat` 模块调用 `model_service.get_active_model('language')` 获取 LLM 配置

---

## 测试要点（待实现）

1. 权限验证：非管理员访问返回 403
2. 列表查询：API Key 正确脱敏
3. 详情查询：单个模型正确返回
4. 更新模型：脱敏值保留原值、新值正确加密、跨字段校验生效
5. 内部接口：`get_active_model` 返回明文 API Key
6. 异常场景：解密失败、模型不存在
