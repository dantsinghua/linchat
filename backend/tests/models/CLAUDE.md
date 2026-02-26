# tests/models 测试指南

> models 模块（LLM 模型配置管理）测试集，覆盖模型层、数据层、服务层、序列化层、视图层及与 Agent 的集成。

---

## 测试文件

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_models.py` | LLMModelConfig 字段约束 / 计算属性 | `models.models` |
| `test_repositories.py` | ModelRepository CRUD / 按类型查询活跃模型 | `models.repositories` |
| `test_serializers.py` | ModelResponseSerializer / ModelUpdateSerializer 验证 | `models.serializers` |
| `test_services.py` | ModelService（SM4 加密/脱敏/CRUD/活跃模型） | `models.services` |
| `test_views.py` | 模型列表/详情/更新视图（鉴权/权限/并发） | `models.views` |
| `test_chat_integration.py` | get_llm 从数据库读取配置 / _get_tool_model_name | `graph.agent` + `models` |

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 models 测试
pytest tests/models/ -v

# 单个文件
pytest tests/models/test_services.py -v

# 带覆盖率
pytest tests/models/ --cov=apps/models --cov-report=term-missing
```

---

## 重要 Fixture 和 Mock

### 核心 Mock

| Mock 目标 | 说明 |
|-----------|------|
| `ModelConfig.objects` | ORM 查询（get/filter/all/order_by） |
| `apps.models.services.sm4_encrypt` / `sm4_decrypt` | SM4 加解密（避免依赖真实密钥） |
| `apps.graph.agent.model_service` | Agent 集成测试中模型服务 |
| `apps.graph.agent.ChatOpenAI` | Agent 集成测试中 LLM 实例创建 |
| `APIRequestFactory` / `force_authenticate` | DRF 视图测试（模拟认证用户） |

### 特殊 Mock 模式

- **test_services.py**: `_mask_api_key` 测试直接调用静态方法，无需 mock
- **test_views.py**: 使用 `@patch.object(ModelRepository, ...)` 替换 Repository 方法
- **test_chat_integration.py**: 同时 mock `model_service` 和 `ChatOpenAI`，验证参数透传

---

## 测试覆盖的功能点

### 模型层（test_models.py）
- 字段约束：模型类型（text/tool/embedding/multimodal）、必填字段
- 可选字段允许 NULL 和 0 值
- 验证器：max_context_window/input_tokens 范围、temperature/top_p/frequency_penalty 范围
- db_table 名称和 str 表示
- 计算属性：effective_context_window（窗口 * 0.9）、masked_api_key

### 数据层（test_repositories.py）
- get_all：按 ID 排序
- get_by_id：存在/不存在
- get_active_by_type：text/tool/embedding/不存在/无效类型
- update：单字段/多字段/NULL/0 值更新

### 序列化层（test_serializers.py）
- **Response**: 全字段输出、API Key 脱敏、NULL 可选字段、effective_context_window
- **Update**: 必填字段验证、API Key 脱敏保留/最短 12 位、容量参数范围
- temperature (0-2) / top_p (0-1) / frequency_penalty (-2 ~ 2) 范围验证
- NULL vs 0 语义区分（NULL=不传递，0=显式传递零值）
- 交叉字段验证：embedding 类型必须设置 embedding_dimensions

### 服务层（test_services.py）
- `_mask_api_key`：长/短/空/None 输入
- `get_all_models`：列表返回、API Key 脱敏、计算属性附加
- `get_by_id`：存在/不存在
- `update_model`：基本更新、脱敏密钥保留原值、新密钥 SM4 加密、不存在404、type/is_active 忽略
- NULL vs 0 语义：NULL 字段不传递给 LLM，0 值显式传递
- `get_active_model`（FR-014）：解密 API Key 返回

### 视图层（test_views.py）
- GET 列表：成功/格式/脱敏/未认证 401/非管理员 403/POST 405/DELETE 405
- GET 详情：成功/不存在 404/脱敏/未认证/非管理员
- PUT 更新：成功/验证失败 400/脱敏保留原值/新密钥加密/不存在/非管理员 403
- 不支持的方法：POST/DELETE/PATCH 405
- 并发更新：Last-Write-Wins 策略

### Agent 集成（test_chat_integration.py）
- get_llm 从数据库读取模型配置
- 仅传递非 NULL 可选参数（FR-007）
- 无 lru_cache，配置变更立即生效
- 使用 max_context_window 而非 effective
- `_get_tool_model_name` 从数据库读取 tool 类型模型名

---

## 注意事项

1. **SM4 加密**: 所有涉及 API Key 的测试 mock 了 SM4 加解密函数
2. **权限控制**: 视图测试区分未认证（401）和非管理员（403），使用 `is_staff` 标志
3. **NULL vs 0 语义**: 多个测试层验证了 NULL（不传递）和 0（显式零值）的区别，这是核心业务语义
4. **无 lru_cache**: 集成测试验证了模型配置不缓存，确保管理员修改立即生效
