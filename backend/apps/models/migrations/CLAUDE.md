# Models 迁移指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

> `apps/models/migrations/` 目录包含 ModelConfig 表的数据库迁移文件。

---

## 迁移文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `0001_create_model_config.py` | 结构迁移 | 创建 `model` 表（16 字段，初始 type: language/embedding） |
| `0002_seed_model_configs.py` | 数据迁移 | 预置 2 条种子记录（language + embedding，从环境变量读取） |
| `0003_add_multimodal_model.py` | 数据迁移 | language -> tool 重命名 + 新增 multimodal 记录 |
| `0004_alter_modelconfig_type.py` | 结构迁移 | type choices 更新为 tool/multimodal/embedding |

---

## 迁移链路

```
0001 (建表: language/embedding choices)
  -> 0002 (种子: language + embedding 记录)
  -> 0003 (数据: language->tool + 新增 multimodal)
  -> 0004 (结构: type choices 改为 tool/multimodal/embedding)
```

最终状态：3 条记录（`tool` / `embedding` / `multimodal`），type choices 与代码一致。

---

## 各迁移详情

### 0001_create_model_config
- CreateModel("ModelConfig") — 表名 `model`
- 初始 type choices: `language` / `embedding`

### 0002_seed_model_configs
- RunPython: 创建 language（从 LLM_API_BASE/KEY/MODEL 环境变量读取）+ embedding（占位值）
- API Key 使用 `sm4_encrypt()` 加密
- 回滚: 删除全部记录

### 0003_add_multimodal_model
- RunPython: 所有 `type="language"` 改为 `type="tool"`
- 新增 `type="multimodal"` 记录（从 LLM_MULTIMODAL_MODEL/GATEWAY_URL/API_KEY 读取）
- 回滚: 删除 multimodal + tool->language

### 0004_alter_modelconfig_type
- AlterField: type choices 更新为 `[("tool", "工具模型"), ("multimodal", "多模态模型"), ("embedding", "向量模型")]`

---

## 注意事项

1. 种子数据依赖环境变量，缺失时使用占位值，需通过模型配置页手动更新
2. 迁移依赖 `apps.users.crypto.sm4_encrypt`，运行前确保该模块可用
3. 不要手动修改迁移文件，用 `makemigrations` 生成新迁移
4. type 枚举演变: `language` -> `tool`（0003），代码中统一使用 `tool/multimodal/embedding`


<claude-mem-context>

</claude-mem-context>