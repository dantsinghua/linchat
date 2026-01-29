# 行为模型 — M1a 模型配置管理

> 行为模型是流程（process）与规则（rule）的绑定层。
> 每个行为 = **一个不可分割的最小化业务动作** + **遵守的规则** + **操作的数据**。

## 1. 行为总览

| 行为 ID | 行为名称 | 绑定规则 | 所属流程 |
|---------|---------|---------|---------|
| B-001 | 查询模型配置 | R-008, R-004, R-005 | P-001 |
| B-002 | 修改模型配置 | R-008, R-001, R-002, R-003, R-004, R-005, R-006, R-007 | P-002 |
| B-003 | 获取激活模型 | R-004 | P-003 |
| B-004 | 构造请求参数 | R-003 | P-003 |

> **注意：** R-008（管理员访问控制）由 DRF 框架层（`IsAdminUser` 权限类）在视图执行前强制校验，B-001/B-002 的业务代码无需显式处理。B-003/B-004 为后端内部调用，不经过 API 层，R-008 不适用。

---

## 2. 行为详细定义

### B-001：查询模型配置

**描述：** 查询所有或单个模型配置，返回脱敏后的数据
**原子性：** 是
**绑定规则：** R-008（管理员访问控制，框架层）、R-004（API Key 解密）、R-005（API Key 脱敏展示）
**操作数据：** 读取 `model` 表

```pseudo
行为: 查询模型配置
输入: {id?: INTEGER}  // 无 id 则查询全部
输出: List[ModelVO] 或 ModelVO
绑定规则: [R-008, R-004, R-005]

执行:
  0. [R-008] 管理员权限校验（框架层自动执行，非管理员 → 403）

  1. 查询数据库:
     IF id 指定:
       model = SELECT * FROM model WHERE id = :id
     ELSE:
       models = SELECT * FROM model ORDER BY type, id

  2. 对每条记录应用 [R-004] + [R-005]:
     plain_key = SM4_DECRYPT(model.api_key, SECRET_KEY)
     model.api_key = MASK(plain_key)
     // 示例: "sk-test1234abcxyz" → "sk-t****cxyz"（前 4 位 + **** + 后 4 位）

  3. 选填字段为 NULL 的，前端展示为"未设置"

  4. RETURN 脱敏后的模型列表/单条
```

```python
class QueryModelBehavior:
    """B-001: 查询模型配置"""
    RULES = ["R-008", "R-004", "R-005"]  # R-008 由框架层 IsAdminUser 保证

    def execute(self, model_id: int = None) -> list[ModelVO]:
        # 查询
        if model_id:
            models = [self.model_repo.find_by_id(model_id)]
        else:
            models = self.model_repo.find_all()

        # [R-004] 解密 + [R-005] 脱敏
        result = []
        for model in models:
            vo = ModelVO.from_entity(model)
            plain_key = sm4_decrypt(model.api_key, SECRET_KEY)
            vo.api_key = mask_key(plain_key)  # sk-t****cxyz
            result.append(vo)
        return result
```

---

### B-002：修改模型配置

**描述：** 校验并保存模型配置修改，配置即时生效
**原子性：** 是
**绑定规则：** R-008, R-001, R-002, R-003, R-004, R-005, R-006, R-007
**操作数据：** 更新 `model` 表

```pseudo
行为: 修改模型配置
输入: {id: INTEGER, data: ModelUpdateDTO}
输出: ModelVO（更新后，api_key 已脱敏）
绑定规则: [R-008, R-001, R-002, R-003, R-004, R-005, R-006, R-007]

执行:
  0. [R-008] 管理员权限校验（框架层自动执行，非管理员 → 403）

  1. [R-007] type 字段不可修改:
     IF data.type != 原记录.type:
       RETURN ERROR "模型类型不可修改"

  2. [R-001] 必填字段校验（PUT 时 6 个必填字段，不含 type）:
     FOR EACH field IN [name, url, api_key, max_context_window,
                        max_input_tokens, max_output_tokens]:
       IF data[field] 为空:
         RETURN ERROR "{field} 不可为空"
     IF data.api_key 不含 "****" AND length(data.api_key) < 12:
       RETURN ERROR "api_key 最少 12 个字符"

  3. [R-002] 选填参数范围校验（闭区间）:
     IF data.temperature IS NOT NULL AND NOT (0 <= data.temperature <= 2):
       RETURN ERROR "temperature 必须在 [0, 2] 范围内"
     // top_p [0,1], frequency_penalty [-2,2], presence_penalty [-2,2] 同理
     // max_context_window, max_input_tokens, max_output_tokens: 非零正整数，不接受小数
     // embedding_dimensions: NULL 或非零正整数，不接受小数

  4. [R-003] NULL 与零值区分（FR-006 存储语义）:
     FOR EACH optional_field IN [temperature, top_p, ...]:
       // 前端传 null → 存 SQL NULL
       // 前端传 0    → 存数值 0
       // 不做任何隐式转换

  5. [R-005] API Key 脱敏检测:
     IF data.api_key 包含 "****":
       // 用户未修改 API Key，保留数据库原值
       data.api_key = 原记录.api_key（加密值）
     ELSE:
       // 用户提交了新 API Key
       [R-004] data.api_key = SM4_ENCRYPT(data.api_key, SECRET_KEY)

  6. 写入数据库:
     UPDATE model SET ... WHERE id = :id
     updated_at = NOW()

  7. [R-006] 配置即时生效:
     // 无缓存，下次读库即为新值

  8. 构造返回值:
     [R-005] 对返回的 api_key 脱敏
     RETURN ModelVO（脱敏后）
```

```python
class UpdateModelBehavior:
    """B-002: 修改模型配置"""
    RULES = ["R-008", "R-001", "R-002", "R-003", "R-004", "R-005", "R-006", "R-007"]  # R-008 由框架层 IsAdminUser 保证

    def execute(self, model_id: int, data: ModelUpdateDTO) -> ModelVO:
        # 查询原记录
        original = self.model_repo.find_by_id(model_id)
        if not original:
            raise NotFoundError("模型不存在")

        # [R-007] type 不可修改
        if data.type and data.type != original.type:
            raise ValidationError("模型类型不可修改")

        # [R-001] 必填字段校验（PUT 时 6 个字段，不含 type）
        # api_key: 新密钥最少 12 字符；脱敏值（含 ****）视为有效，保留原值
        self._validate_required(data)

        # [R-002] 选填参数范围校验（闭区间）+ 整数字段不接受小数
        self._validate_ranges(data)

        # [R-003] NULL/0 区分 — DTO 中 None 和 0 已经是不同值，直接透传（FR-006 存储语义）

        # [R-005] API Key 脱敏检测 + [R-004] 加密
        if "****" in (data.api_key or ""):
            # 未修改，保留原加密值
            data.api_key = original.api_key
        else:
            # 新密钥，加密后存储
            data.api_key = sm4_encrypt(data.api_key, SECRET_KEY)

        # 写入数据库
        updated = self.model_repo.update(model_id, data.to_dict())
        # [R-006] 无缓存，即时生效

        # [R-005] 返回脱敏数据
        vo = ModelVO.from_entity(updated)
        vo.api_key = mask_key(sm4_decrypt(updated.api_key, SECRET_KEY))
        return vo
```

---

### B-003：获取激活模型

**描述：** 按类型获取当前激活的模型配置，用于 AI 调用
**原子性：** 是
**绑定规则：** R-004（API Key 解密）
**操作数据：** 读取 `model` 表

```pseudo
行为: 获取激活模型
输入: {type: STRING}  // 'language' 或 'embedding'
输出: Model（含解密后的 api_key）
绑定规则: [R-004]

执行:
  1. 查询:
     model = SELECT * FROM model
             WHERE type = :type AND is_active = true
             LIMIT 1

  2. IF 不存在:
     RETURN ERROR "没有激活的 {type} 模型，请先在配置页面设置"

  3. [R-004] 解密 API Key:
     model.api_key = SM4_DECRYPT(model.api_key, SECRET_KEY)

  4. RETURN model（含明文 api_key，仅后端内部使用）
```

```python
class GetActiveModelBehavior:
    """B-003: 获取激活模型"""
    RULES = ["R-004"]

    def execute(self, model_type: str) -> Model:
        model = self.model_repo.find_active_by_type(model_type)
        if not model:
            raise ConfigError(f"没有激活的 {model_type} 模型，请先在配置页面设置")

        # [R-004] 解密 API Key（后端内部使用）
        model.api_key = sm4_decrypt(model.api_key, SECRET_KEY)
        return model
```

---

### B-004：构造请求参数

**描述：** 根据模型配置构造 API 请求参数，正确处理 NULL/0 区分
**原子性：** 是
**绑定规则：** R-003（NULL 与零值区分规则，对应 FR-007 调用行为）
**操作数据：** 无数据库操作（纯计算）

```pseudo
行为: 构造请求参数
输入: Model（已解密）
输出: Dict（API 请求参数）
绑定规则: [R-003]

执行:
  1. 基础参数（始终包含）:
     params = {
       "model": model.name
     }

  2. [R-003] 选填参数处理（FR-007 调用行为）:
     FOR EACH field IN [temperature, top_p, frequency_penalty, presence_penalty]:
       value = getattr(model, field)
       IF value IS NOT NULL:     // 包括 value == 0
         params[field] = value
       // value IS NULL → 不加入 params

  3. RETURN params
```

```python
class BuildApiParamsBehavior:
    """B-004: 构造请求参数"""
    RULES = ["R-003"]

    # 仅处理 language 类型的选填采样参数，embedding_dimensions 不参与 API 请求构造
    OPTIONAL_FIELDS = ["temperature", "top_p", "frequency_penalty", "presence_penalty"]

    def execute(self, model: Model) -> dict:
        params = {"model": model.name}

        # [R-003] NULL 不传，0 传入（FR-007 调用行为）
        for field in self.OPTIONAL_FIELDS:
            value = getattr(model, field)
            if value is not None:
                params[field] = value

        return params
```

---

## 3. 规则覆盖矩阵

| 规则 | B-001 查询 | B-002 修改 | B-003 获取激活 | B-004 构造参数 |
|------|-----------|-----------|--------------|-------------|
| R-001 必填校验 | | ✅ | | |
| R-002 范围校验 | | ✅ | | |
| R-003 NULL/0 区分 | | ✅ | | ✅ |
| R-004 加密存储 | ✅ | ✅ | ✅ | |
| R-005 脱敏展示 | ✅ | ✅ | | |
| R-006 即时生效 | | ✅ | | |
| R-007 只改查不增删 | | ✅ | | |
| R-008 管理员访问控制 | ✅(框架层) | ✅(框架层) | | |

---

## 4. 行为与流程编排

```
┌────────────────────────────────────────────────────────┐
│ P-001 查看模型配置                                      │
│                                                        │
│   B-001 查询模型配置                                    │
│     [R-008 权限] → [R-004 解密] → [R-005 脱敏] → 返回   │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ P-002 修改模型配置                                      │
│                                                        │
│   B-002 修改模型配置                                    │
│     [R-008 权限] → [R-007 只改查] → [R-001 必填]        │
│     → [R-002 范围] → [R-003 NULL/0] → [R-005 脱敏检测]  │
│     → [R-004 加密] → 写库 → [R-006 即时生效]            │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ P-003 模型参数构造                                      │
│                                                        │
│   B-003 获取激活模型                                    │
│     [R-004 解密]                                       │
│         │                                              │
│         ▼                                              │
│   B-004 构造请求参数                                    │
│     [R-003 NULL/0 区分]                                │
│         │                                              │
│         ▼                                              │
│     调用模型 API                                       │
└────────────────────────────────────────────────────────┘
```

---

*文档版本：v1.3*
*创建日期：2026-01-29*
*更新日期：2026-01-29 — v1.3: B-002 补充 api_key 最少 12 字符校验、脱敏值通过校验说明、整数字段不接受小数、范围校验闭区间说明*
