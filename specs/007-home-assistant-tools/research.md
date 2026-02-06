# Research: Home Assistant SubAgent

## R-001: HA REST API 通信方式

**Decision**: 使用 httpx.AsyncClient 封装 HA REST API 调用

**Rationale**:
- httpx 已在项目依赖中，无需新增依赖
- 原生 async/await 支持，与 LangGraph 异步执行模型一致
- 支持连接池复用、超时控制、重试策略
- HA REST API 是标准 HTTP + Bearer Token 认证，无需 WebSocket

**Alternatives Considered**:
- `homeassistant-api` Python 库 — 同步调用，需要额外适配异步
- `aiohttp` — 功能等价但项目未使用，增加依赖
- HA WebSocket API — 更实时但增加复杂度，控制/查询场景不需要长连接

## R-002: 速率限制实现方式

**Decision**: 复用 search.py 的 Redis incr+expire 模式，按工具类型分 key

**Rationale**:
- 已验证的模式，search.py 在生产中运行正常
- Redis 基础设施已就绪
- 按 `ha:{tool_type}:rate:{user_id}` 格式分 key，控制/查询/诊断独立限流
- 使用 60 秒窗口而非滑动窗口（简单够用）

**Alternatives Considered**:
- Django 中间件限流 — 粒度太粗，无法区分工具类型
- Token bucket 算法 — 过度复杂，当前规模不需要
- 内存限流 — 多进程部署时不共享

## R-003: 敏感操作确认机制

**Decision**: 在 ha_control 工具内部检测敏感操作，返回确认提示文本而非直接执行

**Rationale**:
- 与现有 SubAgent 架构一致 — 工具返回文本，subagent 将其传递给主 agent，主 agent 转达用户
- 不需要新的确认流程或状态管理
- 敏感设备通过 entity_id 前缀（lock.*, cover.*）和 action（unlock, open_cover）识别
- 未来可扩展为配置化的敏感操作列表

**Alternatives Considered**:
- 二次确认状态机 — 需要在 agent 层面维护状态，复杂度过高
- 前端弹窗确认 — 需要前端改动，违反纯后端范围
- 管理员审批 — 不适合实时控制场景

## R-004: HAClient 实例管理

**Decision**: 每次工具调用创建新的 httpx.AsyncClient 实例（context manager 模式）

**Rationale**:
- 避免全局单例在多用户并发时的连接管理复杂性
- httpx.AsyncClient 创建开销极低（< 1ms）
- HA 请求频率低（单用户 10/min 控制 + 30/min 查询），无需连接池
- Context manager 确保连接正确释放

**Alternatives Considered**:
- 全局单例 — 需要处理连接超时重建、并发安全
- 连接池 — 当前规模下过度工程化
- 模块级缓存 — Django 热重载时可能残留旧连接

## R-005: 设备列表输出截断策略

**Decision**: 按域分组，每域最多显示 20 个设备，超过时显示统计摘要

**Rationale**:
- 避免大量设备导致 SubAgent 输出过长，消耗 token 预算
- 按域分组（light, switch, climate 等）方便用户快速定位
- 20 个/域覆盖大多数家庭场景
- 超出部分用"... 及其他 N 个"替代

**Alternatives Considered**:
- 固定最大 50 个 — 无分组，不便阅读
- 分页 — SubAgent 单次调用，分页不适用
- 只显示活跃设备 — 可能遗漏用户想找的离线设备

## R-006: action 到 HA service 的映射策略

**Decision**: 在 ha_control 内维护一个 ACTION_MAP 字典，将语义化 action 映射到 HA domain/service

**Rationale**:
- 一层抽象，使 subagent LLM 只需理解语义化 action（turn_on, set_brightness），不需了解 HA 内部 service 命名
- 映射关系清晰，易于维护和扩展
- entity_id 的 domain 前缀（light., climate. 等）自动决定目标 service domain

**Alternatives Considered**:
- 让 LLM 直接指定 HA service — 增加 prompt 复杂度，容易出错
- 统一 homeassistant/turn_on — 只支持开关，不支持 set_temperature 等高级操作
