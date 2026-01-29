# 任务清单：模型配置管理

**输入**：设计文档 `/specs/003-model-config/`
**前置条件**：plan.md、spec.md、data-model.md、research.md、contracts/api.yaml、quickstart.md

**测试说明**：宪法第三条要求服务层 95%、总体 80% 覆盖率。测试任务按阶段分组，在对应功能实现后执行。

**组织方式**：任务按用户故事分组，支持每个故事的独立实现和测试。

## 格式：`[编号] [P?] [故事] 描述`

- **[P]**：可并行执行（不同文件，无依赖）
- **[故事]**：所属用户故事（如 US1、US2、US3）
- 描述中包含精确文件路径

---

## 阶段 1：搭建（共享基础设施）

**目的**：创建 Django App 并注册，建立模块目录结构

- [X] T001 创建 `apps/models` Django App 并注册到 `core/settings.py` 的 `INSTALLED_APPS`。完成 App 注册后，执行 `python manage.py shell` 验证 `User.type` 字段存在且值域包含 `'admin'`。若字段不存在，立即报告阻塞问题

---

## 阶段 2：基础层（阻塞前置条件）

**目的**：数据模型、迁移、数据访问层、业务逻辑层——所有 API 和前端的基础

**⚠️ 关键**：所有用户故事必须在本阶段完成后才能开始

- [X] T002 创建 Model 数据模型 in `backend/apps/models/models.py`，包含 16 个字段（type/name/url/api_key/max_context_window/max_input_tokens/max_output_tokens/temperature/top_p/frequency_penalty/presence_penalty/embedding_dimensions/is_active/created_at/updated_at），计算属性 `effective_context_window`（供 M1b 上下文管理使用，M1a 不使用）和 `masked_api_key`，Meta 指定 `db_table = 'model'`。**注意**：`max_context_window`/`max_input_tokens`/`max_output_tokens`/`embedding_dimensions` 使用 `PositiveIntegerField` + `validators=[MinValueValidator(1)]` 确保 > 0（Django `PositiveIntegerField` 允许 0，需额外约束）
- [X] T003 创建数据迁移 in `backend/apps/models/migrations/`，包含 `RunPython` 预置 2 条种子记录：(1) language 类型从环境变量 `LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL_NAME` 读取初始值，容量参数使用默认值（max_context_window=65536, max_input_tokens=32768, max_output_tokens=8192），若环境变量缺失则使用合规占位值（name=`language-placeholder`, url=`https://api.placeholder.com/v1`, api_key=`SM4(placeholder-key!)`, 容量参数同 embedding）并在迁移日志中输出警告；(2) embedding 类型使用合规占位值（name=`text-embedding-placeholder`, url=`https://api.placeholder.com/v1`, api_key=`SM4(placeholder-key!)`, max_context_window=8192, max_input_tokens=8192, max_output_tokens=1, embedding_dimensions=1536）。所有种子数据严格遵循字段校验规则（api_key ≥ 12 字符、整数字段 > 0）。API Key 使用 `apps.users.crypto.sm4_encrypt` 加密存储
- [X] T004 创建 Repository 层 in `backend/apps/models/repositories.py`，实现 `get_all_models()`、`get_model_by_id()`、`get_active_model_by_type()`、`update_model()` 方法，封装 ORM 操作
- [X] T005 创建 Service 层 in `backend/apps/models/services.py`，实现：(1) `get_all_models()` / `get_model_by_id()` — 查询时调用 `sm4_decrypt` 解密 API Key 再脱敏（长度 > 8 时：前 4 位 + `****` + 后 4 位；长度 ≤ 8 时：全部脱敏为 `****`，FR-009）；(2) `update_model()` — 更新时使用 `"****" in api_key` 判断是否为脱敏值，若是则保留原值，否则 `sm4_encrypt` 加密新值（注意：新密钥恰好包含 `****` 子串时会被误判为保留原值，此概率极低可接受）；(3) NULL vs 0 语义正确传递（详见 FR-006 存储语义、FR-007 调用行为）；(4) `get_active_model(type)` — 按类型获取激活模型配置并解密 API Key，供后端内部调用（FR-014），不受管理员权限限制

**检查点**：数据模型、迁移、Repository、Service 就绪，可通过 Django Shell 验证

### 阶段 2 测试

- [X] T023 [P] 编写 Model 数据模型测试 in `backend/tests/apps/models/test_models.py`：字段约束、计算属性 `effective_context_window`（验证为 `int(max_context_window * 0.9)`，该属性供 M1b 使用）和 `masked_api_key`、choices 验证
- [X] T024 [P] 编写 Repository 层测试 in `backend/tests/apps/models/test_repositories.py`：`get_all_models()`、`get_model_by_id()`、`get_active_model_by_type()`、`update_model()` 的正常和异常路径
- [X] T025 编写 Service 层测试 in `backend/tests/apps/models/test_services.py`：SM4 加解密、API Key 脱敏逻辑（含短密钥 ≤8 字符全脱敏为 `****`，FR-009）、`****` 判断保留原值、NULL vs 0 语义传递（显式覆盖 US3 三个验收场景：(1) temperature=NULL 时构造 API 请求不包含该字段；(2) temperature=0 时构造 API 请求包含 temperature:0；(3) 清空与设零的存储状态区分）、`get_active_model(type)` 获取激活模型（覆盖率目标 ≥ 95%）

---

## 阶段 3：用户故事 1 — 查看模型配置（优先级：P1）🎯 MVP

**目标**：管理员打开配置页面，查看语言模型和 embedding 模型的详细参数，API Key 脱敏展示

**独立测试**：打开 `/linchat/settings` 页面，验证 2 条模型记录以卡片形式正确展示，API Key 为脱敏形式

### 用户故事 1 实现

- [X] T006 [P] [US1] 创建响应序列化器 in `backend/apps/models/serializers.py`，实现 `ModelResponseSerializer`，包含所有字段 + `effective_context_window` 计算属性，`api_key` 输出脱敏值。确保 `name`（max 100 字符）和 `url`（max 500 字符）的长度约束由模型层继承
- [X] T007 [P] [US1] 创建 URL 路由 in `backend/apps/models/urls.py` 并注册到 `backend/core/urls.py`，路径 `/api/v1/models/` 和 `/api/v1/models/<int:pk>/`
- [X] T008 [US1] 创建 GET 视图 in `backend/apps/models/views.py`，实现 `ModelListView`（GET 列表）和 `ModelDetailView`（GET 单个），视图仅定义 GET 方法（DRF 自动对未定义方法返回 405，满足 FR-013），调用 Service 层，统一响应格式 `{code, message, data}`。权限控制：在 `backend/apps/models/permissions.py` 中创建自定义 `IsAdminUser` 权限类（基于 `User.type == 'admin'` 判定，不使用 Django 内置 `is_staff`），非管理员返回 403
- [X] T009 [P] [US1] 创建前端类型定义 in `frontend/src/types/model.ts`，定义 `ModelConfig` interface 和 `ModelType` 类型
- [X] T010 [P] [US1] 创建前端 API 服务 in `frontend/src/services/modelService.ts`，实现 `fetchModels()` 和 `fetchModelById()` 方法
- [X] T011 [US1] 创建前端 Zustand Store in `frontend/src/stores/modelStore.ts`，管理模型配置列表状态和加载状态
- [X] T012 [US1] 创建设置页面和组件 in `frontend/src/app/settings/page.tsx` + `frontend/src/components/settings/ModelConfigCard.tsx`，按模型类型以两张配置卡片形式展示（language 卡片和 embedding 卡片），选填参数为 NULL 时显示"未设置"，`embedding_dimensions` 仅对 embedding 类型模型展示。API 错误（403/4xx/5xx）统一路由到已有的错误页面。**前置确认**：验证前端已有错误页面组件及其路径（如 `app/error/page.tsx` 或 Next.js 内置 `error.tsx`），确认 403/404/500 场景的跳转目标
- [X] T013 [US1] 前端权限守卫与导航入口：(1) 修改 `frontend/src/app/chat/page.tsx` 的 header 区域（定位用户名显示与退出按钮所在的容器元素），在用户名与退出按钮之间新增"模型配置"按钮，仅对 admin 用户可见（通过 `useAuth()` 获取用户类型判定），点击跳转到 `/settings` 页面（basePath 自动补 `/linchat`），非管理员用户看不到该入口；(2) 在 `settings/page.tsx` 中实现路由级权限守卫——非管理员通过 URL 直接访问 `/settings` 时，统一路由到已有的错误页面（此为前端权限检查，与 T012 的 API 错误处理互补）

**检查点**：GET API 可用，设置页面以两张卡片正确展示 2 条模型配置，API Key 脱敏

### 阶段 3 测试

- [X] T026 [P] 编写序列化器测试 in `backend/tests/apps/models/test_serializers.py`：`ModelResponseSerializer` 输出字段验证、`api_key` 脱敏输出、`effective_context_window` 计算值
- [X] T027 编写 GET 视图测试 in `backend/tests/apps/models/test_views.py`：列表接口返回 2 条记录、详情接口返回正确数据、统一响应格式 `{code, message, data}` 验证、未认证（匿名）用户请求返回 401、非管理员用户请求返回 403、POST、DELETE、PATCH 请求均返回 405（验证 FR-013 禁止增删和部分更新）
- [X] T031 [P] 编写前端查看功能测试 in `frontend/tests/settings/`：(1) `ModelConfigCard.tsx` — 渲染两张卡片（language/embedding）、API Key 脱敏显示、选填参数 NULL 显示"未设置"、`embedding_dimensions` 仅 embedding 卡片展示；(2) `modelStore.ts` — fetchModels 状态管理、加载状态切换；(3) `modelService.ts` — fetchModels/fetchModelById API 调用 mock 验证；(4) `settings/page.tsx` — 页面加载渲染、API 错误跳转错误页面；(5) Sidebar 设置入口 — 管理员可见、非管理员不可见、非管理员 URL 直接访问路由守卫

---

## 阶段 4：用户故事 2 — 修改模型配置（优先级：P1）

**目标**：管理员修改模型参数并保存，配置即时生效无需重启

**独立测试**：修改某模型的 `name` 和 `temperature`，保存后刷新页面验证更新，发起聊天验证新配置生效

### 用户故事 2 实现

- [X] T014 [P] [US2] 创建更新序列化器 in `backend/apps/models/serializers.py`，实现 `ModelUpdateSerializer`，`type`/`is_active` 设为 `read_only`，选填字段 `required=False, allow_null=True`，验证规则：`name` 非空、最大 100 字符；`url` 非空、最大 500 字符；`api_key` 必填（不可为空），若值包含 `****`（脱敏值）则视为保留原值、跳过长度校验，若为全新密钥则最少 12 字符（FR-005 + FR-012）；`max_context_window`/`max_input_tokens`/`max_output_tokens` 为非零正整数（> 0，不接受小数）；`temperature` [0,2]、`top_p` [0,1]、`frequency_penalty` [-2,2]、`presence_penalty` [-2,2] 闭区间（选填，非空时校验，可置空 NULL）；`embedding_dimensions` 非零正整数或 NULL；增加条件校验：当 `type=language` 时 `embedding_dimensions` 必须为 NULL
- [X] T015 [US2] 创建 PUT 视图 in `backend/apps/models/views.py`，在 `ModelDetailView` 中添加 PUT 方法（视图仅包含 GET + PUT，其他方法由 DRF 自动返回 405，满足 FR-013），调用 Service 层处理 `api_key` 加密逻辑和数据更新，返回统一响应格式
- [X] T016 [P] [US2] 扩展前端 API 服务 in `frontend/src/services/modelService.ts`，添加 `updateModel(id, data)` 方法
- [X] T017 [US2] 创建编辑表单组件 in `frontend/src/components/settings/ModelConfigForm.tsx`，实现：(1) 必填字段校验；(2) 选填字段空字符串 → null、"0" → 0 转换（详见 FR-006 存储语义）；(3) 提交/取消/加载状态；(4) 成功后刷新数据；(5) `embedding_dimensions` 仅对 embedding 类型模型展示编辑项；(6) 数值参数超出范围时（如 temperature > 2）前端即时提示校验错误，阻止提交；(7) 选填参数提供"清除"按钮（清空为 NULL）与数字输入框（可输入 0），明确区分"不设置"和"设为 0"的 UI 交互（US3 验收场景 3）。API 错误统一路由到已有的错误页面
- [X] T018 [US2] 集成编辑功能到设置页面，在 `ModelConfigCard.tsx` 添加编辑按钮，点击展开 `ModelConfigForm.tsx`，保存后更新卡片展示

**检查点**：PUT API 可用，前端可编辑保存模型配置，NULL vs 0 语义正确

### 阶段 4 测试

- [X] T028 [P] 编写 `ModelUpdateSerializer` 测试 in `backend/tests/apps/models/test_serializers.py`：`type`/`is_active` 只读校验、必填字段空值拒绝、`temperature`/`top_p`/`frequency_penalty`/`presence_penalty` 范围校验、NULL vs 0 正确接受
- [X] T032 [P] 编写前端编辑功能测试 in `frontend/tests/settings/`：(1) `ModelConfigForm.tsx` — 必填字段空值校验阻止提交、选填字段空字符串→null 转换、"0"→数字 0 转换、temperature/top_p 等超范围即时校验提示、提交成功后刷新数据、`embedding_dimensions` 仅 embedding 类型展示编辑项；(2) `modelService.ts` — updateModel API 调用 mock 验证；(3) `ModelConfigCard.tsx` — 编辑按钮点击展开表单、保存后卡片内容更新
- [X] T029 编写 PUT 视图测试 in `backend/tests/apps/models/test_views.py`：正常更新、校验失败 400、`api_key` 含 `****` 保留原值、`api_key` 新值加密存储、未认证用户请求返回 401、非管理员用户请求返回 403、并发 PUT 请求验证最后写入优先（两个请求先后修改同一模型的 name 字段，验证最终值为后提交的值）。**注**：405 验证已在 T027 覆盖，此处不重复

---

## 阶段 5：用户故事 4 — 聊天集成（优先级：P1）

**目标**：现有聊天功能无缝切换到数据库配置源，修改配置后即时生效

**独立测试**：修改模型名称后立即发起聊天，验证使用新配置

### 用户故事 4 实现

- [X] T019 [US4] **前置操作**：先全局搜索 `grep -r "LLM_API_BASE\|LLM_API_KEY\|LLM_MODEL_NAME" backend/` 确认环境变量引用的完整范围，确保不遗漏改造点。改造 `backend/apps/chat/agent.py` 和 `backend/apps/chat/services.py`，移除所有 `settings.LLM_*` 环境变量依赖：(1) `agent.py`：移除 `@lru_cache` 和 `settings.LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL_NAME`，改为通过 Service 层 `get_active_model(type)` 从数据库读取 language 类型模型配置，构造 `ChatOpenAI` 实例（仅传入非 NULL 的选填参数，详见 FR-007 调用行为）；(2) `services.py`：将第 591 行 `model_name=settings.LLM_MODEL_NAME` 替换为从数据库获取的模型名称（通过 `get_active_model("language")` 或从已构造的 LLM 实例中获取）。**无缓存设计**：每次请求直接从数据库读取最新配置，不使用 Redis 或内存缓存（M1a 阶段仅 2 条记录，直接读库性能充裕），确保配置修改后即时生效（FR-010）。**注意：模型 API 调用直接使用 `max_context_window` 原始值；`effective_context_window` 是 FR-015 为 M1b 上下文管理预留的计算属性，M1a 不使用**
- [X] T020 [US4] 清理 `backend/core/settings.py` 中的 `LLM_API_BASE`、`LLM_API_KEY`、`LLM_MODEL_NAME` 环境变量配置项。同时检查并清理 `backend/.env` 文件中的 `LLM_API_BASE`、`LLM_API_KEY`、`LLM_MODEL_NAME` 变量（如存在则删除或注释，标记为已迁移到数据库）

**检查点**：聊天功能使用数据库配置，修改配置后即时生效，无需重启

### 阶段 5 测试

- [X] T030 编写聊天集成测试 in `backend/tests/apps/chat/test_agent.py` 和 `backend/tests/apps/chat/test_services.py`：(1) `test_agent.py`：验证 `get_llm()` 从数据库读取 language 模型、仅传入非 NULL 选填参数（详见 FR-007）、无 `lru_cache` 时配置变更即时生效、**验证使用 `max_context_window` 原始值而非 `effective_context_window`**；(2) `test_services.py`：验证 assistant 消息入库时 `model_name` 字段取自数据库模型配置而非环境变量、**管理员修改模型名称后发起新对话，验证新消息记录的 `model_name` 为更新后的值**

---

## 阶段 6：收尾与横切关注点

**目的**：验证和收尾

- [X] T021 执行 quickstart.md 端到端验证：迁移 → Shell 验证 2 条记录 → GET API → PUT API → 前端页面 → 修改模型配置后立即发起聊天验证新配置即时生效（FR-010）→ 聊天集成 → 验证 GET /api/v1/models/ 响应时间 < 200ms（2 条记录直接读库）→ 验证 US3 选填参数精确控制：(1) 将 temperature 设为 0 并保存，GET 验证返回 `temperature: 0`；(2) 清空 temperature 并保存，GET 验证返回 `temperature: null`；(3) 发起聊天请求，确认 temperature=0 时请求包含该参数、temperature=null 时请求不包含该参数
- [X] T033 创建 `backend/apps/models/README.md` 模块文档，说明模块职责、分层结构、API 端点、数据模型概要（宪法第七条要求每个模块目录必须有 README.md）
- [X] T022 提交代码并创建 PR，提交信息遵循宪法 2.3 规范：`feat(models): 模型配置管理——数据库存储、在线修改、即时生效`

---

## 依赖与执行顺序

### 阶段依赖

- **搭建（阶段 1）**：无依赖
- **基础层（阶段 2）**：依赖阶段 1 完成
- **US1 查看（阶段 3）**：依赖阶段 2 完成
- **US2 修改（阶段 4）**：依赖阶段 3 完成（复用 GET 视图和页面基础）
- **US4 集成（阶段 5）**：依赖阶段 2 完成（仅需 Service 层）
- **收尾（阶段 6）**：依赖所有阶段完成

### 用户故事依赖

- **US1 查看**：阶段 2 完成后即可开始
- **US2 修改**：依赖 US1（共用视图和页面）
- **US4 集成**：仅依赖阶段 2，可与 US1 并行

### 用户故事内部依赖

- 后端序列化器和路由可并行（不同文件）
- 前端类型定义和 API 服务可并行
- 视图依赖序列化器
- 页面组件依赖 Store 和 API 服务

### 并行机会

- T006 + T007：序列化器和路由可并行
- T009 + T010：前端类型和 API 服务可并行
- T014 + T016：后端序列化器和前端 API 扩展可并行
- US1 后端（T006~T08）和 US4（T019~T020）可并行（不同模块）

---

## 并行示例：用户故事 1

```text
# 后端并行:
T006: 响应序列化器 in serializers.py
T007: URL 路由 in urls.py

# 前端并行:
T009: 类型定义 in types/model.ts
T010: API 服务 in services/modelService.ts
```

---

## 实施策略

### MVP 优先（仅用户故事 1）

1. 阶段 1：搭建 → 创建 App
2. 阶段 2：基础层 → Model + 迁移 + Repository + Service
3. 阶段 3：US1 → GET API + 设置页面
4. **暂停并验证**：打开设置页面验证 2 条模型展示正确
5. 继续 US2 + US4

### 增量交付

1. 搭建 + 基础层 → 数据层就绪
2. US1 查看 → 配置可见 → 验证（MVP！）
3. US2 修改 → 配置可改 → 验证
4. US4 集成 → 聊天使用数据库配置 → 端到端验证

---

## 备注

- [P] 标记 = 不同文件，无依赖，可并行
- [US*] 标记 = 映射到具体用户故事，便于追溯
- 共 33 个任务：搭建 1 + 基础层 4 + 基础层测试 3 + US1 实现 8 + US1 测试 3（含前端 T031）+ US2 实现 5 + US2 测试 3（含前端 T032）+ US4 实现 2 + US4 测试 1 + 收尾 3（含 README T033）
- 规范中 US3（NULL/0 精确语义）作为横切关注点融入 T005（Service 层）和 T017（表单组件），不单独成阶段。详见 FR-006（存储语义）和 FR-007（调用行为）
- 每个任务或逻辑分组完成后提交代码
