# Feature Specification: Home Assistant SubAgent

**Feature Branch**: `007-home-assistant-tools`
**Created**: 2026-02-05
**Status**: Draft
**Input**: 将 Home Assistant 接入 LinChat SubAgent 体系，实现自然语言控制智能家居设备

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 语音/文字控制设备 (Priority: P1)

用户在 LinChat 中通过自然语言发送指令（如"开客厅灯"、"把卧室空调调到26度"、"关掉所有灯"），系统自动识别意图并通过 Home Assistant 执行对应的设备操作，返回操作结果确认。

**Why this priority**: 设备控制是智能家居的核心价值，覆盖用户最高频的日常使用场景。没有控制能力，其他功能（查询、诊断）就没有意义。

**Independent Test**: 可以通过发送"开灯"指令并在 Home Assistant 中验证灯的状态变化来独立测试，无需查询或诊断功能。

**Acceptance Scenarios**:

1. **Given** 用户已配置 HA 连接且设备在线, **When** 用户发送"开客厅灯", **Then** 系统执行开灯操作并返回确认信息（包含设备名称和当前状态）
2. **Given** 用户发送带参数的控制指令, **When** 用户说"把客厅灯亮度调到50%", **Then** 系统设置亮度为指定值并返回当前亮度确认
3. **Given** 用户请求控制敏感设备（如门锁）, **When** 用户说"解锁前门", **Then** 系统返回确认提示要求用户二次确认，不直接执行
4. **Given** 设备不在线, **When** 用户发送控制指令, **Then** 系统返回设备不可达的提示及可能原因

---

### User Story 2 - 设备状态查询 (Priority: P2)

用户通过自然语言查询设备状态（如"客厅温度多少"、"哪些灯开着"、"空调历史记录"），系统返回人类可读的状态信息。

**Why this priority**: 状态查询是设备控制的补充，用户经常需要先了解设备状态再决定操作。查询功能也是诊断的基础。

**Independent Test**: 可以通过发送"客厅温度多少"并验证返回值与 HA 面板一致来独立测试。

**Acceptance Scenarios**:

1. **Given** 用户想查询单个设备, **When** 用户说"客厅温度多少", **Then** 系统返回该设备当前状态及关键属性
2. **Given** 用户想查看设备列表, **When** 用户说"哪些灯开着", **Then** 系统返回按域分组的设备清单，包含状态摘要
3. **Given** 用户想查看历史, **When** 用户说"客厅空调最近24小时的温度变化", **Then** 系统返回可读的状态变化时间线

---

### User Story 3 - 设备诊断与修复建议 (Priority: P3)

当设备出现异常（不响应、状态异常）时，用户可以请求诊断（如"为什么客厅灯打不开"、"检查智能家居系统"），系统分析可能原因并提供可操作的修复建议。

**Why this priority**: 诊断是高级功能，使用频率低于控制和查询，但在设备异常时能显著提升用户体验，减少手动排查时间。

**Independent Test**: 可以通过模拟设备离线后发送"为什么客厅灯打不开"并验证返回诊断信息来独立测试。

**Acceptance Scenarios**:

1. **Given** 设备状态为 unavailable, **When** 用户说"为什么客厅灯打不开", **Then** 系统返回设备诊断结果，包含可能原因和建议操作
2. **Given** 用户想检查整体系统, **When** 用户说"检查智能家居系统", **Then** 系统返回系统健康状态（版本、运行时间、组件数、自动化状态）
3. **Given** 用户想找不可达设备, **When** 用户说"有哪些设备离线了", **Then** 系统扫描并返回所有不可达设备列表

---

### User Story 4 - 条件启用与优雅降级 (Priority: P1)

系统管理员配置 HA 连接信息后，智能家居功能自动启用。未配置时，系统完全不暴露任何 HA 相关能力，不影响其他聊天功能。

**Why this priority**: 条件启用是基础架构要求，确保未配置 HA 的用户不受影响。与 P1 并列是因为它是所有 HA 功能的前提。

**Independent Test**: 可以通过分别在有/无 HA 配置的环境启动系统，验证功能可见性来独立测试。

**Acceptance Scenarios**:

1. **Given** 管理员在环境变量中配置了 HA_URL 和 HA_TOKEN, **When** 系统启动, **Then** ha_subagent 自动注册到主 agent 工具列表
2. **Given** 环境变量中未配置 HA_URL 或 HA_TOKEN, **When** 系统启动, **Then** ha_subagent 不注册，主 agent 不显示任何智能家居能力
3. **Given** HA 配置有效但 HA 服务不可达, **When** 用户发送控制指令, **Then** 系统返回友好的连接错误提示而非系统错误

---

### Edge Cases

- 用户使用模糊设备名（如"灯"而非"客厅灯"）时如何处理？ — subagent 先查询设备列表匹配
- HA 服务中途断开时如何处理？ — 返回连接错误提示，不影响其他功能
- 用户短时间内发送大量控制指令时如何处理？ — 速率限制，超限后提示稍后再试
- 用户请求控制被加入黑名单的设备时如何处理？ — 返回设备已被禁止控制的提示
- HA 返回的设备列表非常大（100+设备）时如何处理？ — 按域分组，截断输出
- 设备操作需要较长时间（如空调开机预热）时如何处理？ — 返回操作已发送的确认，不等待设备达到目标状态
- HA Token 过期或无效时如何处理？ — 返回认证失败提示，建议检查 Token 配置

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须通过 SubAgent 架构集成 Home Assistant，ha_subagent 作为主 agent 的一个工具对外暴露
- **FR-002**: ha_subagent 内部必须管理三个专属工具：ha_query（状态查询）、ha_control（设备控制）、ha_diagnose（诊断修复）
- **FR-003**: 系统必须支持条件注册 — 仅在 HA_URL 和 HA_TOKEN 均已配置时注册 ha_subagent
- **FR-004**: 设备控制必须支持以下设备类型：灯光（含亮度/色温）、开关、空调（含温度/模式）、风扇、窗帘、门锁、媒体播放器、场景、脚本
- **FR-005**: 敏感操作必须返回确认提示，不直接执行。敏感操作定义：L3 级别（门锁解锁、车库门开启）和 L4 级别（禁用自动化规则），完整列表见 data-model.md 敏感操作识别表
- **FR-006**: 状态查询必须支持三种模式：单设备详情、按域分组的设备列表、指定时间范围的历史记录
- **FR-007**: 诊断功能必须支持：系统健康检查、单设备诊断、不可达设备扫描、自动化规则检查、错误日志查看
- **FR-008**: 系统必须实现按用户粒度的速率限制：控制操作 10次/分钟、查询操作 30次/分钟、诊断操作 5次/分钟
- **FR-009**: 系统必须支持设备黑名单（HA_BLOCKED_ENTITIES），黑名单中的设备控制操作直接拒绝
- **FR-010**: 所有 HA API 错误必须在 subagent 内部捕获并转换为人类可读的文本返回，不得向主 agent 抛出异常
- **FR-011**: HA HTTP 通信必须统一超时控制（默认 10 秒），subagent 整体执行不超过 60 秒

### Key Entities

- **HAClient**: Home Assistant REST API 的 HTTP 客户端封装，负责所有与 HA 实例的通信，包括设备状态获取、服务调用、历史查询、系统配置获取
- **ha_subagent**: SubAgent 工具函数，对主 agent 暴露为单个工具，内部通过 react agent 管理三个 HA 专属工具
- **HA 配置**: 连接 Home Assistant 所需的配置信息，包括实例地址（HA_URL）、访问令牌（HA_TOKEN）、请求超时、设备黑名单
- **设备实体 (Entity)**: HA 中的设备抽象，由 entity_id（如 light.living_room）唯一标识，包含状态、属性、域等信息

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户通过自然语言发送设备控制指令后，3 秒内收到操作确认回复
- **SC-002**: 设备状态查询返回结果与 Home Assistant 面板显示一致，准确率 100%
- **SC-003**: 未配置 HA 的用户完全感知不到智能家居功能的存在，不影响正常聊天体验
- **SC-004**: 新增 SubAgent 仅需修改不超过 2 个已有文件（符合架构扩展性约束）
- **SC-005**: 所有 HA API 错误场景均返回人类可读的提示信息，无系统级错误暴露给用户
- **SC-006**: 敏感操作（门锁、车库门等）100% 需要用户二次确认才能执行
- **SC-007**: 速率限制在超限时正确触发，返回友好提示而非系统错误

## Assumptions

- Home Assistant 实例已部署且可通过内网或 frp 穿透访问
- 用户已在 HA 中创建 Long-Lived Access Token
- HA 实例使用标准 REST API（/api/states、/api/services 等）
- httpx 库已在项目依赖中
- Redis 基础设施已就绪（用于速率限制）
- SubAgent 架构（base.py、run_subagent、get_common_tools）已由 006-subagent-tools 建立

## Dependencies

- 006-subagent-tools 特性已完成（SubAgent 基础架构）
- Home Assistant 实例可网络访问
- Redis 服务运行中（速率限制）
