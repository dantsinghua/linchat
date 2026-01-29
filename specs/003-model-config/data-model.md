# 数据模型 — M1a 模型配置管理

**特性**：003-model-config
**日期**：2026-01-29

## 1. 实体概览

```
┌──────────────────┐
│     model        │  （单表，无外键关联）
│  （模型配置表）    │
└──────────────────┘
```

> M1a 仅涉及 `model` 单表。后续 M1b 通过 `model.max_context_window` 读取参数。

---

## 2. 字段定义

| 字段 | 类型 | 约束 | 说明 | 必填 |
|------|------|------|------|------|
| id | SERIAL PK | 自增主键 | — | 自动 |
| type | VARCHAR(20) | NOT NULL, ENUM | 模型类型：`language` / `embedding` | **必填** |
| name | VARCHAR(100) | NOT NULL | 模型名称，如 `deepseek-chat` | **必填** |
| url | VARCHAR(500) | NOT NULL | API 基础地址 | **必填** |
| api_key | VARCHAR(500) | NOT NULL, MIN 12 chars | API Key（SM4 加密存储，明文最少 12 字符） | **必填** |
| max_context_window | INTEGER | NOT NULL, > 0 | 最大上下文窗口（token 数） | **必填** |
| max_input_tokens | INTEGER | NOT NULL, > 0 | 最大输入 token 数 | **必填** |
| max_output_tokens | INTEGER | NOT NULL, > 0 | 最大输出 token 数 | **必填** |
| temperature | FLOAT | NULL, [0, 2] | 温度参数，NULL = 使用模型默认值 | 选填 |
| top_p | FLOAT | NULL, [0, 1] | Top-P 采样，NULL = 使用模型默认值 | 选填 |
| frequency_penalty | FLOAT | NULL, [-2, 2] | 频率惩罚，NULL = 使用模型默认值 | 选填 |
| presence_penalty | FLOAT | NULL, [-2, 2] | 存在惩罚，NULL = 使用模型默认值 | 选填 |
| embedding_dimensions | INTEGER | NULL, > 0 | 向量维度（仅 embedding 类型有效） | 选填 |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | 系统管理，管理员不可编辑 | 系统 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 | 自动 |
| updated_at | TIMESTAMP | NOT NULL, auto_now | 更新时间 | 自动 |

### 字段分类

- **必填字段（7 个）：** type, name, url, api_key, max_context_window, max_input_tokens, max_output_tokens
  - 其中 `type` 在初始迁移时为必填，但 PUT 接口中为**只读字段**（不可修改），故 PUT 校验的必填字段为 6 个（不含 type）。详见 spec FR-005。
- **选填字段（5 个）：** temperature, top_p, frequency_penalty, presence_penalty, embedding_dimensions
- **系统字段（4 个）：** id, is_active, created_at, updated_at

---

## 3. 选填字段语义（核心：NULL ≠ 0）

| 值状态 | 数据库存储 | 含义 | API 请求行为（FR-007） |
|--------|-----------|------|----------------------|
| 用户未设置 | NULL | 使用模型默认值 | **不传入**该参数 |
| 用户设为 0 | 0 | 明确设为零值 | **传入** `parameter: 0` |
| 用户设为具体值 | 具体值 | 使用指定值 | **传入** `parameter: value` |

前端处理：空字符串 `""` → JSON `null` → SQL NULL；`"0"` → 数字 `0` → SQL 0

> **交叉引用：** 存储语义详见 FR-006，调用行为详见 FR-007

---

## 4. 验证规则

| 字段 | 验证规则 |
|------|----------|
| type | 枚举值：`language`, `embedding`，**PUT 时不可修改** |
| name | 非空，最大 100 字符 |
| url | 非空，最大 500 字符（不校验格式，允许任意非空字符串） |
| api_key | 非空，明文最少 12 字符，存储时 SM4 加密，展示时脱敏（长度 > 8 时：前 4 位 + `****` + 后 4 位；长度 ≤ 8 时：全部脱敏为 `****`） |
| max_context_window | 非零正整数（> 0，不接受小数） |
| max_input_tokens | 非零正整数（> 0，不接受小数） |
| max_output_tokens | 非零正整数（> 0，不接受小数） |
| temperature | NULL 或 0 ≤ value ≤ 2 |
| top_p | NULL 或 0 ≤ value ≤ 1 |
| frequency_penalty | NULL 或 -2 ≤ value ≤ 2 |
| presence_penalty | NULL 或 -2 ≤ value ≤ 2 |
| embedding_dimensions | NULL 或非零正整数（> 0，不接受小数，仅 type=embedding 时有效） |

---

## 5. 初始数据种子

通过 Django migration `RunPython` 预置 2 条记录：

| type | name | url | api_key | max_context_window | max_input_tokens | max_output_tokens | embedding_dimensions |
|------|------|-----|---------|-------------------|-----------------|-----------------|---------------------|
| language | `{LLM_MODEL_NAME}` | `{LLM_API_BASE}` | `SM4({LLM_API_KEY})` | 65536 | 32768 | 8192 | NULL |
| embedding | `text-embedding-placeholder` | `https://api.placeholder.com/v1` | `SM4(placeholder-key!)` | 8192 | 8192 | 1 | 1536 |

> **种子数据说明：** 所有种子记录严格遵循字段校验规则（api_key ≥ 12 字符、整数字段 > 0、embedding_dimensions 仅 embedding 类型有值）。embedding 种子记录使用合规占位值填充，管理员需在配置页面修改为实际值后方可使用 embedding 功能。
>
> 迁移时一次性从环境变量读取 language 配置，写入后环境变量不再使用（数据库为唯一来源）。若环境变量缺失，language 记录使用与 embedding 相同策略的合规占位值并在迁移日志中输出警告。

---

## 6. 计算属性（不存表，运行时计算）

| 属性 | 公式 | 说明 |
|------|------|------|
| effective_context_window | `int(max_context_window * 0.9)` | 预留 10% 安全余量，**供 M1b 上下文管理使用。M1a 阶段模型 API 调用直接使用 `max_context_window` 原始值** |
| masked_api_key | 长度 > 8 时：前 4 位 + `****` + 后 4 位；长度 ≤ 8 时：`****` | GET 接口返回值，不存储。示例：`sk-test1234abcxyz` → `sk-t****cxyz`；短密钥 `abcd1234` → `****` |

---

## 7. 状态说明

- `is_active` 始终为 `true`（M1a 阶段系统管理，管理员不可编辑）
- 无状态转换、无软删除、无关联关系（独立实体）

---

## 8. DDL 定义

```sql
CREATE TABLE model (
    id SERIAL PRIMARY KEY,
    type VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL,
    url VARCHAR(500) NOT NULL,
    api_key VARCHAR(500) NOT NULL,  -- 明文最少 12 字符（应用层校验）
    max_context_window INTEGER NOT NULL CHECK (max_context_window > 0),
    max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens > 0),
    max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens > 0),
    temperature FLOAT CHECK (temperature >= 0 AND temperature <= 2),
    top_p FLOAT CHECK (top_p >= 0 AND top_p <= 1),
    frequency_penalty FLOAT CHECK (frequency_penalty >= -2 AND frequency_penalty <= 2),
    presence_penalty FLOAT CHECK (presence_penalty >= -2 AND presence_penalty <= 2),
    embedding_dimensions INTEGER CHECK (embedding_dimensions > 0),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 9. Django 模型映射

```
Django app: apps.models
表名: model
Django 模型类: Model
```

| Django 字段 | Django Field 类型 |
|-------------|-------------------|
| id | AutoField (PK) |
| type | CharField(max_length=20, choices=[('language','language'),('embedding','embedding')]) |
| name | CharField(max_length=100) |
| url | CharField(max_length=500) |
| api_key | CharField(max_length=500) |
| max_context_window | PositiveIntegerField(validators=[MinValueValidator(1)]) |
| max_input_tokens | PositiveIntegerField(validators=[MinValueValidator(1)]) |
| max_output_tokens | PositiveIntegerField(validators=[MinValueValidator(1)]) |
| temperature | FloatField(null=True, blank=True) |
| top_p | FloatField(null=True, blank=True) |
| frequency_penalty | FloatField(null=True, blank=True) |
| presence_penalty | FloatField(null=True, blank=True) |
| embedding_dimensions | PositiveIntegerField(null=True, blank=True) |
| is_active | BooleanField(default=True) |
| created_at | DateTimeField(auto_now_add=True) |
| updated_at | DateTimeField(auto_now=True) |

---

*文档版本：v2.4（整合版）*
*创建日期：2026-01-29*
*更新日期：2026-01-29 — v2.4: Django 映射补充 MinValueValidator(1)；种子数据全部符合字段校验规则（移除豁免）；补充 embedding_dimensions 列*
*更新日期：2026-01-29 — v2.3: 补充 api_key 最少 12 字符约束；整数字段明确不接受小数；脱敏规则补充短密钥处理（≤8 字符全脱敏）；URL 校验说明*
