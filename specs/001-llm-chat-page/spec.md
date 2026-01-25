# Feature Specification: 大模型聊天页面

**Feature Branch**: `001-llm-chat-page`
**Created**: 2026-01-25
**Status**: Draft

---

## ⚠️ 强制参考文档

> **开发/实施前必须阅读以下文档，本规范仅包含验收场景和需求索引。**

| 文档 | 路径 | 内容简介 |
|------|------|----------|
| **数据模型** | `specs/001-llm-chat-page/data-model.md` | PostgreSQL表结构（sys_user、message、langgraph_execution）、Redis缓存设计、LangGraph RedisSaver配置、实体关系图 |
| **流程模型** | `specs/001-llm-chat-page/process-model.md` | 登录流程P_AUTH_001、Token鉴权P_AUTH_002、消息发送P_CHAT_001、历史加载P_CHAT_002的完整时序图和代码示例 |
| **行为模型** | `specs/001-llm-chat-page/behavior-model.md` | 6个原子行为的完整实现：验证码生成、用户登录、Token验证、消息发送、Agent执行、历史加载，含Python代码模板 |
| **规则模型** | `specs/001-llm-chat-page/rule-model.md` | 12条业务规则：验证码有效期、登录锁定、Token双重过期、消息长度限制、数据隔离、Agent超时等，含配置参数 |

---

## Clarifications

### Session 2026-01-25

- Q: 登录失败锁定策略？ → A: 连续 5 次失败后锁定账户 15 分钟
- Q: 验证码有效期？ → A: 2 分钟过期，前端自动刷新新验证码
- Q: 多租户隔离级别？ → A: 租户即用户，共享数据库通过消息表的用户唯一标识隔离数据
- Q: LangGraph checkpointer 持久化方案？ → A: 使用 Redis 缓存存储，终止是临时状态无需持久化
- Q: 主数据库选型？ → A: 统一使用 PostgreSQL（与 Langfuse 共用，简化运维）

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 用户登录认证 (Priority: P1)

用户打开聊天页面时，系统首先展示登录界面。用户需要输入用户名、密码，并完成图形验证码验证后才能进入聊天界面。登录成功后获取的 token 默认有24小时有效期，同时叠加一个判断：即如果用户在 1 小时内没有任何操作，token 将自动过期，用户在进行任何操作时都会被重定向到登录页面让用户重新登录。

**Why this priority**: 认证是所有功能的前置条件，没有认证就无法进行聊天，属于核心基础设施。

**Independent Test**: 可以独立测试登录流程，验证用户名密码验证、验证码（使用captcha库，见research.md）校验、token 生成与过期机制，无需依赖聊天功能。

**Acceptance Scenarios**:

1. **Given** 路由保护：用户访问聊天页面且未登录, **When** 系统检测到无有效 token, **Then** 自动跳转到登录页面
2. **Given** 用户在登录页面, **When** 输入正确的用户名、密码和正确的验证码, **Then** 登录成功使用SM4加密生成token（格式：`SM4({username}|{password}|{captcha}|{timestamp})`）、设置token有效期并开始监听用户操作日志/事件并跳转到聊天界面，用户事件包括任何页面点击、请求、页面刷新、浏览器回退等，不包括系统响应，例如大模型完成回复等，
3. **Given** 用户在登录页面, **When** 输入错误的验证码, **Then** 显示验证码错误提示，不允许登录
4. **Given** 用户在登录页面, **When** 输入错误的用户名或密码, **Then** 统一显示错误提示"用户名或密码错误"（防止用户名枚举攻击）
5. **Given** 用户已登录且 token 在 1 小时内有效, **When** 用户进行任何操作, **Then** 操作正常执行且无操作过期时间刷新（最长1小时），但登录后24小时绝对过期，不可延长
6. **Given** 用户已登录但 1 小时内无操作, **When** 用户尝试发送消息或做任何其他操作、请求, **Then** 系统提示"Token已过期，请重新登录"并跳转到登录页面

> 📖 **详细实现**: 登录流程见 `process-model.md#P_AUTH_001`，Token规则见 `rule-model.md#R_TOKEN_003`

---

### User Story 2 - 发送消息并获取 AI 流式响应 (Priority: P1)

已登录用户在聊天界面可以输入消息并发送给 AI 助手。AI 助手的响应以流式方式逐字显示在界面上，用户可以实时看到响应内容的生成过程。所有消息（包括用户消息和 AI 响应）都会持久化存储到数据库中。

**Why this priority**: 这是聊天应用的核心功能，直接体现产品价值。

**Independent Test**: 可以使用已登录用户发送测试消息，验证消息发送、流式响应显示、消息持久化的完整流程。

**Acceptance Scenarios**:

1. **Given** 用户已登录并进入聊天界面, **When** 在输入框输入消息并点击发送, **Then** 消息立即显示在对话区域，并开始接收 AI 流式响应
2. **Given** 用户发送消息后, **When** AI 开始生成响应, **Then** 响应内容以流式方式逐步显示，有明显的打字效果
3. **Given** AI 正在生成响应, **When** 响应包含 Markdown 格式, **Then** 内容实时渲染为格式化的 HTML
   - 支持格式：标题(h1-h6)、有序/无序列表、表格、代码块(含语法高亮)、加粗、斜体、删除线、制表符缩进
   - 扩展格式：下划线（通过HTML `<u>` 标签渲染，需启用rehype-raw插件）
4. **Given** AI 正在生成响应, **When** 响应包含 Mermaid 流程图语法, **Then** 流式完成后渲染为可视化图形（<500ms，见SC-006）
5. **Given** 用户发送消息成功, **When** 刷新页面或重新登录, **Then** 历史消息从数据库加载并正确显示；如果存在进行中的流式输出（通过message.status=2判断），前端自动重新建立SSE连接继续接收
6. **Given** 用户有历史聊天, **When** 登录并进入聊天界面, **Then** 历史消息按时间顺序显示在对话区域，所有会话必须按照正确的时间顺序展示(用户问：按照后端langgraph 对话agent接收时间；大模型回：按照后端回复的首个token生成时间开始算，之后按时间顺序正序进行展示)
7. **Given** 用户 A 有聊天记录, **When** 用户 B 登录, **Then** 用户 B 看不到用户 A 的任何聊天记录
8. **Given** 用户登录后, **When** 聊天记录默认锚定最新聊天区域，即最底部，向上滑动则可以浏览历史内容, **Then** 可以查看更早的历史消息
9. **Given** 用户成功发送聊天数据, **When** langgraph agent正在生成响应，**Then** 此时的聊天框发送按钮变更为停止按钮，点击停止按钮则：
   - 终止langgraph agent继续生成
   - 保存当前checkpoint（状态为terminated）
   - 已生成的消息显示"[已中断]"标记
   - 消息status更新为3（中断）
   - 弹出提示"响应已中断，如有需要请复制已显示内容"
   - **assistant消息框显示"继续生成"按钮**：
     - 点击"继续生成"：恢复该checkpoint，从中断处继续生成
     - 若用户在输入框输入新问题并发送：该中断的checkpoint作废，基于新消息创建新的对话轮次
10. **Given** 用户成功发送聊天数据, **When** langgraph agent如果失败了，**Then** 则用户问依然停留在输入框内，不在聊天记录列表生成用户问的对话框，聊天记录数据不存储该数据，但是该数据将会计入日志，用户可以点击发送按钮再次进行尝试，直到正常返回

> 📖 **详细实现**: 消息流程见 `process-model.md#P_CHAT_001`，Agent执行见 `behavior-model.md#B_CHAT_002`

---

### User Story 3 - 系统配置管理 (Priority: P2)

通过配置文件统一管理数据库连接、缓存配置、LLM 接口参数等配置项。配置变更后系统重启即可读取新配置（当前版本不支持运行时热更新）。配置文件可以由开发人员、claude code根据所需配置进行初始化

**Why this priority**: 配置管理是运维基础，但不直接影响用户功能，可作为辅助功能实现。

**Independent Test**: 可以修改配置文件中的参数，验证系统是否正确读取和应用新配置。

**Acceptance Scenarios**:

1. **Given** 配置文件包含数据库连接信息, **When** 系统启动, **Then** 成功连接到配置的数据库
2. **Given** 配置文件包含 LLM 接口地址, **When** 用户发送消息, **Then** 请求发送到配置的 LLM 服务地址
3. **Given** 配置文件包含 Redis 缓存配置, **When** 系统运行, **Then** 缓存服务正常工作

> 📖 **详细实现**: 配置参数见 `data-model.md#七、配置参数汇总`

---

### User Story 4 - LangGraph Agent 监控 (Priority: P3)

开发人员可以通过 LangGraph Dev 界面查看 Agent 的运行状态和调用链路。通过 Langfuse 可以监控 LLM 调用的性能指标和成本。

**Why this priority**: 监控是运维和调试需求，对终端用户无直接影响，可作为增强功能实现。

**Independent Test**: 可以发送测试消息，然后在 LangGraph Dev 和 Langfuse 界面验证调用记录和指标。

**Acceptance Scenarios**:

1. **Given** 用户发送消息触发 LangGraph Agent, **When** 查看 LangGraph Dev 界面, **Then** 可以看到 Agent 的执行流程和节点状态
2. **Given** LLM 调用发生, **When** 查看 Langfuse 监控面板, **Then** 可以看到调用延迟、token 用量等指标

> 📖 **详细实现**: 执行监控表见 `data-model.md#langgraph_execution`，Langfuse集成见 `behavior-model.md#B_CHAT_002`

---

### Edge Cases

| 场景 | 处理策略 |
|------|----------|
| 网络中断时发送消息 | 提示网络错误，保留用户输入内容在输入框内 |
| LLM 服务不可用 | 显示友好错误提示"AI 服务暂时无法连接，请稍后重试"，自动重试3次 |
| LLM 响应超时 | 显示"AI 响应超时，请稍后重试"，自动重试3次 |
| LLM 内容过滤 | 显示"消息包含敏感内容，请修改后重试"，不重试，允许用户修改输入 |
| LLM 配额用尽 | 显示"服务配额用尽，请联系管理员"，不重试 |
| LLM 响应异常 | 显示"AI 响应异常，请稍后重试"，自动重试3次 |
| 流式响应中断 | 已接收内容保留显示，消息末尾添加"[已中断]"标记，后端记录status=3，弹出提示"响应已中断，如有需要请复制已显示内容" |
| 验证码过期 | 用户点击刷新验证码，生成新图片 |
| Token 过期但页面未关闭 | 任何 API 返回 401 时跳转登录页（统一401页面，蓝白风格） |
| 超长消息 | 限制单条消息最大 4000 字符 |
| 并发登录 | 单点登录，最新登录有效；服务端通过SSE推送登出事件，被踢出的会话显示"您已在其他设备登录"（停留3秒）后自动跳转登录页 |
| 空消息发送 | 前端阻止，trim()首尾空格后校验 |
| 快速重复点击 | 前端防抖处理（300ms间隔），防止用户快速重复点击发送按钮 |

> 📖 **详细规则**: 见 `rule-model.md#R_MSG_001`、`R_STREAM_001`

---

## Requirements *(mandatory)*

### Functional Requirements 索引

> **完整实现细节见各模型文档，此处仅作索引。**

| 编号 | 需求描述 | 详细文档 |
|------|----------|----------|
| **用户认证** |||
| FR-001 | 登录页面（用户名/密码/验证码） | `behavior-model.md#B_AUTH_002` |
| FR-002/002a | 验证码生成与自动刷新（2分钟有效） | `behavior-model.md#B_AUTH_001`、`rule-model.md#R_CAPTCHA_*` |
| FR-003 | 国密算法Token生成（SM3哈希/SM4加密） | `rule-model.md#R_TOKEN_001` |
| FR-004 | Token双重过期（24h绝对+1h无操作） | `rule-model.md#R_TOKEN_003` |
| FR-005 | Token验证与401跳转 | `behavior-model.md#B_AUTH_003` |
| FR-006 | 密码SM3哈希存储 | `data-model.md#sys_user` |
| FR-007 | 初始化admin账户 | `data-model.md#初始化数据` |
| FR-008/008a | 无注册功能 + 5次失败锁定15分钟 | `rule-model.md#R_LOGIN_001` |
| **聊天功能** |||
| FR-009 | 单用户单会话模式 | `data-model.md#设计决策` |
| FR-010~012 | 消息实时持久化 + 流式响应 | `behavior-model.md#B_CHAT_001/002` |
| FR-013~014 | Markdown/Mermaid实时渲染 | `process-model.md#P_CHAT_001` |
| FR-015~016 | 历史加载 + 用户数据隔离 | `behavior-model.md#B_CHAT_003`、`rule-model.md#R_DATA_001` |
| **LangGraph Agent** |||
| FR-017~018a | ReAct Agent + Redis Checkpointer | `behavior-model.md#B_CHAT_002`、`data-model.md#RedisSaver` |
| FR-019~020 | LangGraph Dev + Langfuse集成 | `data-model.md#langgraph_execution` |
| **配置服务** |||
| FR-021~025 | 统一配置（DB/Redis/LLM/环境变量） | `data-model.md#七、配置参数汇总` |
| **监控埋点** |||
| FR-026~027 | 监控字段 + 扩展字段预留 | `data-model.md#message表`、`langgraph_execution表` |

---

### Key Entities 索引

> **完整表结构见 `data-model.md`**

| 实体 | 存储 | 说明 | 详细定义 |
|------|------|------|----------|
| sys_user | PostgreSQL | 用户表（认证、锁定、统计） | `data-model.md#2.1` |
| message | PostgreSQL | 消息表（持久化聊天记录） | `data-model.md#2.2` |
| langgraph_execution | PostgreSQL | 执行监控表（可选） | `data-model.md#2.3` |
| auth:token:* | Redis | Token缓存（双重过期） | `data-model.md#3.1` |
| auth:captcha:* | Redis | 验证码缓存（2分钟TTL） | `data-model.md#3.1` |
| langgraph:checkpoint:* | Redis | LangGraph对话状态 | `data-model.md#3.2` |

---

## Success Criteria *(mandatory)*

| 编号 | 指标 | 目标值 | 测量定义 |
|------|------|--------|----------|
| SC-001 | 登录流程完成时间 | < 30秒 | 从用户点击"登录"按钮到成功跳转聊天页面完成的前端感知时间（不含验证码获取时间，仅测量登录提交到跳转完成） |
| SC-002 | 首个响应字符延迟 | < 2秒 | 从用户发送消息到收到第一个SSE chunk的时间 |
| SC-003 | 流式字符延迟 | < 100ms | 相邻SSE chunk之间的间隔时间 |
| SC-004 | 并发用户支持 | ≥ 100 | 100用户同时发送消息，系统无死锁/数据错乱 |
| SC-005 | 历史消息加载（50条） | < 2秒 | 从API请求发出到前端渲染完成的时间 |
| SC-006 | Markdown/Mermaid渲染 | < 500ms | 从SSE收到done事件到DOM渲染完成的时间 |
| SC-007 | 认证拦截准确率 | 100% | 未登录访问受保护页面被正确拦截的比例 |
| SC-008 | 消息持久化成功率 | 100% | 发送成功的消息在刷新后仍存在的比例 |
| SC-009 | 用户数据隔离 | 100% | 用户A无法看到用户B消息的验证通过率 |

---

## Assumptions

1. **LLM 服务可用性**: vLLM 内网服务稳定，支持 OpenAI 兼容流式 API
2. **网络环境**: 内网延迟可接受
3. **用户规模**: 初期 < 100 并发，暂不考虑集群
4. **浏览器兼容**: Chrome/Firefox/Edge/Safari 最新两版本
5. **单设备登录**: 最新登录有效，旧设备 token 失效
6. **消息长度**: 用户消息 ≤ 4000 字符，AI 响应无限制

---
