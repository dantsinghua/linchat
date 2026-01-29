# M1a: 模型配置管理 - 需求规划文档

## 1. 概述

### 1.1 背景
linchat 当前的模型配置信息写在环境变量中，不利于动态管理和前端配置。需要将模型注册信息迁移到 PostgreSQL，构建模型配置页面。

### 1.2 目标
- 将模型配置从环境变量迁移到 PostgreSQL `model` 表
- 构建模型配置页面，支持查看和修改
- 为后续上下文管理、记忆系统提供模型参数基础

---

## 2. 功能需求

### 2.1 数据模型 — `model` 表

| 字段 | 类型 | 说明 | 必填 |
|------|------|------|------|
| id | SERIAL PK | 主键 | — |
| type | VARCHAR | 模型类型：`language` / `embedding`（后续可扩展） | **必填** |
| name | VARCHAR | 模型名称，如 `gpt-4o`、`text-embedding-3-large` | **必填** |
| url | VARCHAR | API Base URL | **必填** |
| api_key | VARCHAR | API Key（加密存储） | **必填** |
| max_context_window | INTEGER | 最大上下文窗口（token 数） | **必填** |
| max_input_tokens | INTEGER | 最大输入 token 数 | **必填** |
| max_output_tokens | INTEGER | 最大输出 token 数 | **必填** |
| temperature | FLOAT | 温度参数 | 选填 |
| top_p | FLOAT | Top-P 采样 | 选填 |
| frequency_penalty | FLOAT | 频率惩罚 | 选填 |
| presence_penalty | FLOAT | 存在惩罚 | 选填 |
| embedding_dimensions | INTEGER | 向量维度（仅 embedding 类型） | 选填 |
| is_active | BOOLEAN | 是否为当前激活模型 | — |
| created_at | TIMESTAMP | 创建时间 | — |
| updated_at | TIMESTAMP | 更新时间 | — |

> 所有参数遵循 OpenAI API 标准，确保兼容 OpenAI API 兼容接口。

### 2.2 选填参数处理规则

- 选填参数为空（NULL）时，**不传入该参数**给模型 API
- **空值 ≠ 零值**：`temperature = NULL` 表示不设置（使用模型默认值），`temperature = 0` 表示设置为 0
- 前端表单中选填参数留空 → 存储为 NULL → 构造 API 请求时跳过该字段
- 前端需区分"清空"（设为 NULL）和"设为 0"两种操作

```python
# 构造模型请求时的参数处理示例
def build_model_params(model: Model) -> dict:
    params = {"model": model.name}
    # 选填参数：仅在非 NULL 时传入
    if model.temperature is not None:
        params["temperature"] = model.temperature
    if model.top_p is not None:
        params["top_p"] = model.top_p
    if model.frequency_penalty is not None:
        params["frequency_penalty"] = model.frequency_penalty
    if model.presence_penalty is not None:
        params["presence_penalty"] = model.presence_penalty
    return params
```

### 2.3 配置页面功能

- **查看**：展示当前配置的语言模型和 embedding 模型
- **修改**：编辑模型参数（URL、API Key、参数等）
- **不需要增删**：模型记录由系统初始化预置，用户只做修改
- 固定两种模型类型：`language` 和 `embedding`（类型字段支持后续扩展）
- 前端表单：必填字段标星号，选填字段支持清空

---

## 3. 技术架构

### 3.1 数据库设计

```sql
CREATE TABLE model (
    id SERIAL PRIMARY KEY,
    type VARCHAR(20) NOT NULL,  -- 'language' | 'embedding'
    name VARCHAR(100) NOT NULL,
    url VARCHAR(500) NOT NULL,
    api_key VARCHAR(500),
    max_context_window INTEGER,
    max_input_tokens INTEGER,
    max_output_tokens INTEGER,
    temperature FLOAT,
    top_p FLOAT,
    frequency_penalty FLOAT,
    presence_penalty FLOAT,
    embedding_dimensions INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 初始化预置数据
INSERT INTO model (type, name, url, api_key, max_context_window, max_input_tokens, max_output_tokens)
VALUES 
    ('language', '', '', '', 0, 0, 0),
    ('embedding', '', '', '', 0, 0, 0);
```

### 3.2 接口定义

```python
class ModelService:
    async def get_active_model(self, type: str) -> Model:
        """获取当前激活的模型配置"""
    
    async def get_all_models(self) -> List[Model]:
        """获取所有模型配置"""
    
    async def update_model(self, id: int, data: dict) -> Model:
        """更新模型配置（只改查，不增删）"""
    
    def get_effective_context_window(self, model: Model) -> int:
        """获取有效上下文窗口 = max_context_window * 0.9"""
        return int(model.max_context_window * 0.9)
```

### 3.3 API 端点

```
GET    /api/models          → 获取所有模型配置
GET    /api/models/:id      → 获取单个模型配置
PUT    /api/models/:id      → 更新模型配置
```

---

## 4. 验收标准

- [ ] 模型数据存储在 PostgreSQL，不依赖环境变量
- [ ] 配置页面可查看和修改语言模型及 embedding 模型参数
- [ ] 修改后立即生效，无需重启
- [ ] 必填字段校验：type、name、url、api_key、max_context_window、max_input_tokens、max_output_tokens
- [ ] 选填参数为 NULL 时不传入 API 请求
- [ ] 选填参数为 0 时传入 0（区分 NULL 和 0）
- [ ] API Key 不在前端明文展示（脱敏）

---

## 5. 依赖与风险

### 5.1 依赖
- PostgreSQL

### 5.2 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 模型配置错误 | API 调用失败 | 前端校验 + 后端校验 |
| API Key 泄露 | 安全问题 | 加密存储 + 前端脱敏 |

---

## 6. 排期建议

| 阶段 | 内容 | 预估时间 |
|------|------|----------|
| Phase 1 | model 表 + 数据迁移 + API | 0.5-1 天 |
| Phase 2 | 配置页面前端 | 0.5-1 天 |
| Phase 3 | 现有逻辑对接（替换环境变量读取） | 0.5 天 |

**总计：约 1.5-2 天**

---

*文档版本：v1.0*
*创建日期：2026-01-29*
*作者：小鱼*
