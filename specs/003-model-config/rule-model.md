# 规则模型 — M1a 模型配置管理

## 1. 规则总览

| 规则 ID | 规则名称 | 适用行为 |
|---------|---------|---------|
| R-001 | 必填字段校验规则 | B-002 修改模型配置 |
| R-002 | 选填参数范围校验规则 | B-002 修改模型配置 |
| R-003 | NULL 与零值区分规则 | B-002 修改, B-004 构造请求参数 |
| R-004 | API Key 加密存储规则 | B-001 查看, B-002 修改, B-003 获取激活模型 |
| R-005 | API Key 脱敏展示规则 | B-001 查看, B-002 修改（返回脱敏数据） |
| R-006 | 配置即时生效规则 | B-002 修改模型配置 |
| R-007 | 只改查不增删规则 | 全局约束 |
| R-008 | 管理员访问控制规则 | B-001 查询, B-002 修改（框架层强制） |

---

## 2. 规则详细定义

### R-001：必填字段校验规则

- **描述：** 修改模型配置时，必填字段不可为空
- **适用时机：** 后端接收 PUT 请求时、前端表单提交前
- **备注：** `type` 字段在初始迁移时为必填（决定模型类型，对应前端两张配置卡片），但 PUT 接口中为**只读字段**（不可修改），故 PUT 校验的必填字段为 6 个（不含 `type`）。详见 spec FR-005

```pseudo
规则: 必填字段校验
输入: 提交的模型配置数据 model_data
输出: {valid: BOOLEAN, errors?: List[STRING]}

初始迁移必填字段列表（7 个）:
  - type:               不可为空，值必须为 'language' 或 'embedding'
  - name:               不可为空字符串
  - url:                不可为空字符串
  - api_key:            不可为空字符串
  - max_context_window: 不可为空，必须为正整数
  - max_input_tokens:   不可为空，必须为正整数
  - max_output_tokens:  不可为空，必须为正整数

PUT 接口必填字段列表（6 个，排除 type）:
  - name, url, api_key, max_context_window, max_input_tokens, max_output_tokens

额外约束:
  - api_key: 字符串输入，明文最少 12 个字符（PUT 时传入脱敏值含 **** 视为有效，保留原值）
  - max_context_window, max_input_tokens, max_output_tokens: 非零正整数（> 0，不接受小数）

处理:
  errors = []
  FOR EACH field IN 必填字段列表:
    IF model_data[field] 为空 OR NULL:
      errors.append(f"{field} 不可为空")
  IF api_key 不含 "****" AND length(api_key) < 12:
    errors.append("api_key 最少 12 个字符")
  IF errors 非空:
    RETURN {valid: false, errors: errors}
  RETURN {valid: true}
```

---

### R-002：选填参数范围校验规则

- **描述：** 选填参数如果有值（非 NULL），必须在合法范围内
- **适用时机：** 后端接收 PUT 请求时

```pseudo
规则: 选填参数范围校验
输入: 提交的模型配置数据 model_data
输出: {valid: BOOLEAN, errors?: List[STRING]}

范围约束（浮点参数为选填，非空时必须满足对应闭区间，也可置空 NULL）:
  - temperature:        NULL 或 [0, 2] 闭区间（小数）
  - top_p:              NULL 或 [0, 1] 闭区间（小数）
  - frequency_penalty:  NULL 或 [-2, 2] 闭区间（小数）
  - presence_penalty:   NULL 或 [-2, 2] 闭区间（小数）
  - embedding_dimensions: NULL 或 非零正整数（> 0，不接受小数）

条件约束:
  - 当 type = 'language' 时，embedding_dimensions 必须为 NULL

处理:
  errors = []
  FOR EACH (field, min, max) IN 范围约束:
    value = model_data[field]
    IF value IS NOT NULL:
      IF value < min OR value > max:
        errors.append(f"{field} 必须在 [{min}, {max}] 范围内")
  IF errors 非空:
    RETURN {valid: false, errors: errors}
  RETURN {valid: true}
```

---

### R-003：NULL 与零值区分规则

- **描述：** 选填参数的 NULL 和 0 具有完全不同的语义，系统必须全链路区分
- **适用时机：** 前端表单处理、后端存储、API 请求构造
- **对应需求：** FR-006（存储语义）、FR-007（调用行为）

```pseudo
规则: NULL 与零值区分
适用链路: 前端 → 后端 → 数据库 → API 请求构造

1. 前端表单:
   - 用户清空输入框 → 提交 NULL
   - 用户输入 0    → 提交 0
   - 前端需提供明确的交互区分（如清空按钮 vs 输入 0）

2. 后端存储（FR-006 存储语义）:
   - 接收 NULL → 存储为 SQL NULL
   - 接收 0   → 存储为数值 0
   - 不可将 NULL 默认转为 0，也不可将 0 默认转为 NULL

3. API 请求构造（FR-007 调用行为）:
   - 字段值为 NULL → 请求参数中不包含该字段
   - 字段值为 0   → 请求参数中包含 field: 0

伪代码:
  def build_api_params(model):
    params = {"model": model.name}
    FOR EACH field IN [temperature, top_p, frequency_penalty, presence_penalty]:
      value = getattr(model, field)
      IF value IS NOT NULL:
        params[field] = value    # 包括 value == 0 的情况
      # value IS NULL → 不加入 params
    RETURN params
```

---

### R-004：API Key 加密存储规则

- **描述：** API Key 在数据库中必须加密存储，不可明文
- **算法：** SM4（国密对称加密）
- **适用时机：** 写入数据库前加密，读取使用时解密

```pseudo
规则: API Key 加密存储
算法: SM4 对称加密

写入时:
  encrypted_key = SM4_ENCRYPT(plain_api_key, SECRET_KEY)
  存储 encrypted_key 到 model.api_key 字段

读取时（后端内部使用）:
  plain_api_key = SM4_DECRYPT(model.api_key, SECRET_KEY)
  用 plain_api_key 构造 API 请求

约束:
  - SECRET_KEY 从配置文件读取，不硬编码
  - 明文 API Key 不写入日志
  - 解密操作仅在构造 API 请求时执行
```

---

### R-005：API Key 脱敏展示规则

- **描述：** 前端展示模型配置时，API Key 必须脱敏，不可明文
- **适用时机：** GET 接口返回数据时

```pseudo
规则: API Key 脱敏展示
输入: plain_api_key
输出: masked_api_key

处理:
  IF length(plain_api_key) <= 8:
    masked_api_key = "****"            // 短密钥全部脱敏
  ELSE:
    prefix = plain_api_key[0:4]
    suffix = plain_api_key[-4:]
    masked_api_key = prefix + "****" + suffix

  // 示例: "sk-test1234abcxyz" → "sk-t****cxyz"（前 4 位 + **** + 后 4 位）
  // 短密钥示例: "abcd1234" → "****"（长度 ≤ 8，全脱敏）
  // 注意: api_key 明文最少 12 字符（R-001），正常情况不会出现 ≤ 8 的密钥，此为防御性处理

约束:
  - GET /api/v1/models/ 返回的 api_key 字段始终为脱敏值
  - 完整 API Key 仅在 PUT 时接收写入，读取时永远脱敏
  - PUT 时如果传入的 api_key 与脱敏格式匹配（含 ****），视为未修改，保留原值
```

---

### R-006：配置即时生效规则

- **描述：** 模型配置修改保存后，下一次 API 调用立即使用新配置
- **适用时机：** PUT 保存成功后

```pseudo
规则: 配置即时生效
机制: 无缓存，每次 API 调用从数据库实时读取

处理:
  1. 管理员保存修改 → 数据写入 model 表
  2. 下一次 AI 对话请求 → 从 model 表读取最新配置
  3. 无需重启服务、无需刷新缓存

约束:
  - 不在内存中缓存模型配置（M1a 阶段流量小，直接读库）
  - 如果未来需要缓存，缓存失效策略由新规则定义
```

---

### R-007：只改查不增删规则

- **描述：** M1a 阶段只支持查看和修改模型配置，不支持新增和删除
- **适用时机：** 全局约束，API 层面强制
- **对应需求：** FR-013

```pseudo
规则: 只改查不增删

允许的操作:
  - GET    /api/v1/models/       → 查看所有（允许）
  - GET    /api/v1/models/:id/   → 查看单个（允许）
  - PUT    /api/v1/models/:id/   → 修改（允许）

禁止的操作:
  - POST、DELETE、PATCH 等其他所有方法 → 返回 405 Method Not Allowed

约束:
  - 视图仅定义 GET 和 PUT 方法，DRF 对未定义方法自动返回 405（对应 spec FR-013）
  - 模型记录由数据库初始化脚本预置（language + embedding 各一条）
  - type 字段在 PUT 时不可修改（防止改变模型类型）
```

---

### R-008：管理员访问控制规则

- **描述：** 模型配置 API 仅允许管理员用户访问，非管理员返回 403；前端侧边栏仅对管理员显示设置入口，非管理员通过 URL 直接访问时统一路由到已有的错误页面
- **适用时机：** 框架层（DRF `IsAdminUser` 权限类），在视图执行业务逻辑前强制校验
- **对应需求：** FR-016（API 权限）、FR-017（前端页面权限）
- **宪法依据：** 第六条 6.1 异常分类 — PermissionDeniedError → 403

```pseudo
规则: 管理员访问控制
适用端点: GET /api/v1/models/, GET /api/v1/models/:id/, PUT /api/v1/models/:id/
实现层: DRF 权限类（IsAdminUser），视图层声明

处理:
  1. API 请求到达视图:
     user = request.user
     IF NOT user.is_authenticated:
       RETURN 401 "未登录或登录已过期"
     IF user.type != 'admin':
       RETURN 403 "权限不足，仅管理员可访问"

  2. 前端入口控制:
     IF 当前用户角色 != admin:
       侧边栏不显示设置入口（非管理员看不到）
       通过 URL 直接访问 /settings → 统一路由到已有的错误页面

约束:
  - 基于 User.type 字段判定（type='admin' 为管理员，type='user' 为普通用户）
  - 不使用 Django 内置 is_staff/is_superuser，自定义 IsAdminUser 权限类
  - 权限校验在业务逻辑之前执行（框架层保证）
  - B-003/B-004 为后端内部调用，不经过 API 层，不受此规则约束
```

---

## 3. 规则依赖关系

```
PUT /api/v1/models/:id/ 请求到达
    │
    ├─▶ R-008 管理员访问控制（框架层：DRF IsAdminUser，非管理员 → 403）
    │
    ├─▶ R-007 只改查不增删（路由层：确认是 PUT，非 POST/DELETE）
    │
    ├─▶ R-001 必填字段校验（PUT 时 6 个必填字段，不含 type）
    │
    ├─▶ R-002 选填参数范围校验
    │
    ├─▶ R-003 NULL 与零值区分（存储前，FR-006 存储语义）
    │
    ├─▶ R-004 API Key 加密存储（写入前加密）
    │
    └─▶ R-006 配置即时生效（写入后立即可读到新值）


GET /api/v1/models/ 请求到达
    │
    ├─▶ R-008 管理员访问控制（框架层：DRF IsAdminUser，非管理员 → 403）
    │
    ├─▶ R-004 API Key 解密（内部）
    │
    └─▶ R-005 API Key 脱敏展示（返回前脱敏）


AI 对话构造请求时（后端内部，不经过 API 层，R-008 不适用）
    │
    ├─▶ R-004 API Key 解密（获取明文密钥）
    │
    └─▶ R-003 NULL 与零值区分（构造请求参数，FR-007 调用行为）
```

---

*文档版本：v1.3*
*创建日期：2026-01-29*
*更新日期：2026-01-29 — v1.3: R-001 补充 api_key 最少 12 字符约束及脱敏值通过校验说明；R-002 明确闭区间和小数/整数类型；R-005 补充短密钥防御性脱敏说明*
