# Models 迁移指南

> `apps/models/migrations/` 目录包含 ModelConfig 表的数据库迁移文件。

---

## 迁移文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `0001_create_model_config.py` | 结构迁移 | 创建 `model` 表，16 个字段（含 type choices: language/embedding） |
| `0002_seed_model_configs.py` | 数据迁移 | 预置 2 条种子记录：language + embedding（从环境变量读取初始值） |
| `0003_add_multimodal_model.py` | 数据迁移 | language->tool 类型重命名 + 新增 multimodal 记录 |

---

## 迁移链路

```
0001_create_model_config  (建表)
    |
    v
0002_seed_model_configs   (种子数据: language + embedding)
    |
    v
0003_add_multimodal_model (language->tool 重命名 + 新增 multimodal)
```

最终状态：表中有 3 条记录，类型分别为 `tool` / `embedding` / `multimodal`。

---

## 各迁移详情

### 0001_create_model_config

- **操作**: `CreateModel("ModelConfig")`
- **表名**: `model`
- **字段**: id(AutoField PK), type, name, url, api_key, max_context_window, max_input_tokens, max_output_tokens, temperature, top_p, frequency_penalty, presence_penalty, embedding_dimensions, is_active, created_at, updated_at
- **初始 type choices**: `language` / `embedding`
- **注意**: 模型代码中 type choices 已更新为 `tool` / `multimodal` / `embedding`，但此迁移文件中仍为旧值（Django 迁移不影响运行时 choices 校验）

### 0002_seed_model_configs

- **操作**: `RunPython(seed_model_configs, reverse_seed)`
- **创建记录**:
  1. **language 模型**: 从环境变量 `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL_NAME` 读取；缺失则使用占位值
  2. **embedding 模型**: 使用占位值（`text-embedding-placeholder`）
- **API Key 处理**: 使用 `sm4_encrypt()` 加密存储
- **回滚**: 删除所有记录

### 0003_add_multimodal_model

- **操作**: `RunPython(rename_and_add_multimodal, reverse_migration)`
- **步骤**:
  1. 将所有 `type="language"` 记录改为 `type="tool"`
  2. 新增 `type="multimodal"` 记录，从环境变量 `LLM_MULTIMODAL_MODEL` / `LLM_GATEWAY_URL` / `LLM_GATEWAY_API_KEY` 读取
- **回滚**: 删除 multimodal 记录 + tool->language 重命名

---

## 注意事项

1. **种子数据依赖环境变量**: 迁移 0002/0003 在 `migrate` 时从环境变量读取值。如果环境变量缺失，会使用占位值，需后续通过模型配置页面手动更新
2. **SM4 加密依赖**: 种子数据迁移导入了 `apps.users.crypto.sm4_encrypt`，运行迁移前需确保该模块可用
3. **不要手动修改迁移文件**: 如需调整数据模型，使用 `makemigrations` 生成新迁移
4. **type 枚举演变**: `language` -> `tool`（0003 迁移），新代码中统一使用 `tool` / `multimodal` / `embedding`
