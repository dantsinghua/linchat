# Models 模块开发指南

> 本文件为 `apps/models` 模型配置管理模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

管理 LLM 模型配置（工具模型 + 多模态模型 + 嵌入模型），支持在线查看和修改，配置修改后即时生效无需重启。

**当前阶段**: 固定 3 条记录（`tool` + `multimodal` + `embedding`），仅支持查看和修改，不支持创建、删除、停用。

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
├── apps.py            # Django App 配置（ModelsConfig）
├── admin.py           # Django Admin（空）
├── tests.py           # 测试文件（空，测试位于 tests/models/）
├── __init__.py        # 模块初始化（空）
└── migrations/        # 数据库迁移（详见 migrations/CLAUDE.md）
    ├── __init__.py
    ├── 0001_create_model_config.py   # 建表迁移
    ├── 0002_seed_model_configs.py    # 种子数据（language + embedding）
    └── 0003_add_multimodal_model.py  # language->tool 重命名 + 新增 multimodal
```

---

## 核心数据模型

### ModelConfig（表名：`model`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | AutoField (PK) | 自增主键 |
| `type` | CharField(20) | `tool` / `multimodal` / `embedding` |
| `name` | CharField(100) | 模型名称 |
| `url` | CharField(500) | API 基础地址 |
| `api_key` | CharField(500) | API 密钥（**SM4 加密存储**） |
| `max_context_window` | PositiveIntegerField | 最大上下文窗口（token 数） |
| `max_input_tokens` | PositiveIntegerField | 最大输入 token 数 |
| `max_output_tokens` | PositiveIntegerField | 最大输出 token 数 |
| `temperature` | FloatField (NULL) | 温度参数 0-2（NULL=使用模型默认值） |
| `top_p` | FloatField (NULL) | Top-P 采样 0-1 |
| `frequency_penalty` | FloatField (NULL) | 频率惩罚 -2~2 |
| `presence_penalty` | FloatField (NULL) | 存在惩罚 -2~2 |
| `embedding_dimensions` | PositiveIntegerField (NULL) | 向量维度（仅 embedding 类型有效） |
| `is_active` | BooleanField | 是否激活（系统管理，不可编辑） |
| `created_at` | DateTimeField (auto_now_add) | 创建时间 |
| `updated_at` | DateTimeField (auto_now) | 更新时间 |

**类型枚举常量**:
- `TYPE_TOOL = "tool"` -- 工具模型（原 language，用于文本推理 + Agent 工具调用）
- `TYPE_MULTIMODAL = "multimodal"` -- 多模态模型（图像/视频理解，如 MiniCPM-o）
- `TYPE_EMBEDDING = "embedding"` -- 向量模型（文本向量化）

**计算属性**:
- `effective_context_window` -> `int(max_context_window * 0.9)`（预留 10% 安全余量）
- `masked_api_key` -> 脱敏展示：长度>8 时 `前4位****后4位`，否则全脱敏（内部先 SM4 解密再脱敏）

---

## API 端点

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/v1/models/` | AdminUser | 获取所有模型配置（API Key 脱敏） |
| GET | `/api/v1/models/<id>/` | AdminUser | 获取单个模型配置 |
| PUT | `/api/v1/models/<id>/` | AdminUser | 更新模型配置 |

所有端点仅管理员可访问，普通用户返回 403。未定义的 HTTP 方法（POST/DELETE 等）由 DRF 自动返回 405。

---

## 核心业务逻辑

### API Key 三态处理

| 场景 | 形态 | 说明 |
|------|------|------|
| 数据库存储 | SM4 密文 | `sm4_encrypt(明文)` |
| GET 响应 | 脱敏展示 | `sk-a****bKey`（长度<=8 时全脱敏） |
| PUT 请求 | 脱敏值或新值 | 含 `****` 则保留原值；否则加密存储新值（最少 12 字符） |
| 内部调用 | 明文 | `sm4_decrypt(密文)`，解密失败返回空字符串 |

### 更新流程

```
PUT 请求 -> ModelUpdateSerializer 校验
  -> 检查 api_key：含 "****" -> 移除（保留原值）；否则 -> sm4_encrypt(新值)
  -> 移除不可修改字段（type/is_active/id/created_at/updated_at）
  -> model_repo.update() -> 返回脱敏数据
```

### 内部接口（供其他模块调用）

```python
from apps.models.services import model_service

# 获取激活的工具模型（API Key 解密为明文）
config = model_service.get_active_model("tool")
# config = {id, type, name, url, api_key(明文), max_context_window, ...}

# 获取激活的多模态模型
config = model_service.get_active_model("multimodal")

# 获取激活的嵌入模型
config = model_service.get_active_model("embedding")
```

此接口供 `apps.chat` / `apps.memory` / `apps.graph` 模块调用，不受管理员权限限制。

---

## 分层架构

### 视图层（views.py）

| 类 | 方法 | 说明 |
|----|------|------|
| `ModelListView` | GET | 获取所有模型配置列表 |
| `ModelDetailView` | GET/PUT | 获取单个 / 更新模型配置 |

使用 CBV（APIView），权限类 `IsAdminUser`，业务逻辑委托给 services。

### 序列化层（serializers.py）

| 序列化器 | 说明 |
|---------|------|
| `ModelResponseSerializer` | GET 响应格式化（含脱敏 API Key + effective_context_window 计算属性） |
| `ModelUpdateSerializer` | PUT 请求校验 |

**`ModelUpdateSerializer` 校验规则**:
- `validate_api_key()`: 脱敏值（含 `****`）跳过长度校验，新密钥最少 12 字符
- `validate_max_context_window()`: 确保整数
- `validate()`: 跨字段校验 -- 非 embedding 类型的 `embedding_dimensions` 必须为 NULL（通过 `context["model_type"]` 获取类型）

### 服务层（services.py）

`ModelService` 类（全局实例: `model_service`），所有方法为 `@staticmethod`：

| 方法 | 说明 |
|------|------|
| `get_all_models()` | 查询全部模型（API Key 脱敏） |
| `get_model_by_id(model_id)` | 查询单个（API Key 脱敏） |
| `update_model(model_id, data)` | 更新模型配置（处理 API Key 三态） |
| `get_active_model(model_type)` | 获取激活模型（API Key **明文**，内部调用） |

辅助函数：
- `_mask_api_key(decrypted_key)`: 脱敏处理
- `_model_to_dict(model, api_key_value)`: 模型转字典
- `_to_dict_with_key(model, decrypt=False)`: 统一转换（decrypt=True 返回明文）

### 数据访问层（repositories.py）

`ModelRepository` 类（全局实例: `model_repo`），所有方法为 `@staticmethod`：

| 方法 | 说明 |
|------|------|
| `get_all()` | 查询全部（按 id 排序） |
| `get_by_id(model_id)` | 按 ID 查询 |
| `get_active_by_type(model_type)` | 按类型获取激活模型（type + is_active=True） |
| `update(model, **kwargs)` | 更新字段并保存 |

### 权限层（permissions.py）

`IsAdminUser`: 基于 `request.user_type == "admin"` 判定（由 TokenAuthMiddleware 设置）。非管理员返回 403。

---

## 数据库迁移

| 迁移 | 内容 |
|------|------|
| `0001_create_model_config` | 创建 `model` 表，定义全部 16 字段 |
| `0002_seed_model_configs` | 种子数据：创建 language + embedding 两条记录，从环境变量读取初始值 |
| `0003_add_multimodal_model` | `language` -> `tool` 重命名 + 新增 `multimodal` 记录（MiniCPM-o） |

---

## 开发规范

### 分层职责（严格遵守）

- **views.py**: 仅做 HTTP 解析和响应包装，禁止业务逻辑
- **services.py**: 封装所有业务逻辑，是核心层
- **repositories.py**: 封装 ORM 操作，提供全局实例
- **serializers.py**: 请求校验和响应格式化

### 同步模式

本模块当前使用**同步**模式（与 `apps/memory` 的异步模式不同），因为模型配置操作频率低，仅管理员使用，无需高并发支持。

### 安全红线

- API Key **必须** SM4 加密存储，禁止明文入库
- GET 响应**必须**脱敏展示，禁止返回明文或密文
- 更新时含 `****` 的值必须保留原值，防止覆盖
- `is_active` / `type` 等系统字段禁止通过 API 修改
- 解密失败返回空字符串而非抛出异常（防御性编程）

---

## 关键依赖

| 依赖 | 位置 | 说明 |
|------|------|------|
| SM4 加解密 | `apps/users/crypto.py` | `sm4_encrypt()` / `sm4_decrypt()` / `sm4_decrypt_safe()` |
| 统一响应 | `apps/common/responses.py` | `ApiResponse` 格式 |
| Token 中间件 | `apps/common/middleware.py` | 设置 `request.user_type` |

### 被依赖

| 调用方 | 调用方式 | 类型参数 |
|--------|---------|---------|
| `apps.chat` / `apps.graph` | `model_service.get_active_model("tool")` | 工具模型配置 |
| `apps.chat` | `model_service.get_active_model("multimodal")` | 多模态模型配置 |
| `apps.memory` | `model_service.get_active_model("embedding")` | Embedding 模型配置 |
| `apps.memory.tasks` | `model_service.get_active_model("tool")` | 语言模型预热 |

---

## 测试方法

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行 models 模块测试
pytest tests/models/ -v

# 带覆盖率
pytest tests/models/ --cov=apps.models --cov-report=term-missing
```

### 测试要点

1. 权限验证：非管理员访问返回 403
2. 列表查询：API Key 正确脱敏，3 条记录全部返回
3. 详情查询：单个模型正确返回
4. 更新模型：脱敏值保留原值、新值正确加密、跨字段校验生效
5. 内部接口：`get_active_model` 返回明文 API Key
6. 异常场景：解密失败、模型不存在
7. 类型枚举：tool / multimodal / embedding 三种类型正确处理
